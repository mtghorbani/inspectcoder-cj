"""Replayable immutable inputs and explicit guarded candidate application."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import platform
import stat
import tempfile
from pathlib import Path

from .jobs import config_for, read_record
from .project import Project, Toolchain, trusted_path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def identity(path: Path) -> dict:
    return {"path": str(path.resolve(strict=True)), "sha256": digest(path)}


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def record_invocation(project: Project, tools: Toolchain, output: Path) -> dict:
    config = config_for(project)
    record = {
        "schema_version": 1,
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "release": platform.release(),
        },
        "engine": {path.name: digest(path) for path in sorted(Path(__file__).parent.glob("*.py"))},
        "root": str(project.root.resolve()),
        "cwd": str(project.root.resolve()),
        "config": config,
        "acceptance_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True).encode()
        ).hexdigest(),
        "sources": {name: identity(project.root / name) for name in project.sources},
        "files": {name: identity(project.root / name) for name in project.files},
        "inputs": [identity(Path(path)) for path in project.inputs],
        "toolchain": {
            "cjc": identity(Path(tools.cjc)),
            "cjdb": identity(Path(tools.cjdb)),
            "sysroot": tools.sysroot,
            "runtime": tools.runtime,
        },
    }
    if project.native:
        record["toolchain"]["cjpm"] = identity(Path(project.native["cjpm"]))
    write_json(output / "invocation.json", record)
    write_json(output / "project.json", config)
    return record


def validate_inventory(record: dict, output: Path) -> None:
    trusted_path(Path(record["root"]))
    for item in [*record["sources"].values(), *record.get("files", {}).values(), *record["inputs"]]:
        trusted_path(Path(item["path"]))
    if record["config"].get("native"):
        from .native import inventory

        current = inventory(Path(record["root"]), ignored_roots=(output.resolve(),))
        if current != sorted(record["config"]["files"]):
            raise ValueError("native acceptance inventory changed; resolve a fresh repair")


def replay_project(output: Path, tools: Toolchain | None = None) -> Project:
    record = read_record(output)
    validate_inventory(record, output)
    project = Project.from_config(Path(record["root"]), record["config"])
    tools = tools or Toolchain.discover(project.sysroot)
    if (
        record["acceptance_sha256"]
        != hashlib.sha256(json.dumps(record["config"], sort_keys=True).encode()).hexdigest()
    ):
        raise ValueError("recorded acceptance contract changed")
    for item in [*record["sources"].values(), *record.get("files", {}).values(), *record["inputs"]]:
        path = Path(item["path"])
        if path.is_symlink() or not path.is_file() or digest(path) != item["sha256"]:
            raise ValueError(f"replay input changed: {path}")
    for name in ("cjc", "cjdb"):
        if identity(Path(getattr(tools, name))) != record["toolchain"][name]:
            raise ValueError(f"replay toolchain changed: {name}")
    if (
        tools.sysroot != record["toolchain"]["sysroot"]
        or tools.runtime != record["toolchain"]["runtime"]
    ):
        raise ValueError("replay toolchain environment changed")
    if project.native and identity(Path(project.native["cjpm"])) != record["toolchain"]["cjpm"]:
        raise ValueError("replay cjpm toolchain changed")
    return project


def finish_candidate(project: Project, output: Path) -> None:
    record = read_record(output)
    candidate = output / "repaired.cj"
    original = project.root / project.editable
    write_json(
        output / "candidate.json",
        {
            "schema_version": 1,
            "target": str(original.absolute()),
            "editable": project.editable,
            "base_sha256": record["sources"][project.editable]["sha256"],
            "candidate_sha256": digest(candidate),
        },
    )
    baseline = output / "versions/0.cj"
    diff = difflib.unified_diff(
        (baseline if baseline.is_file() else original).read_text().splitlines(keepends=True),
        candidate.read_text().splitlines(keepends=True),
        fromfile=project.editable,
        tofile=project.editable + " (candidate)",
    )
    (output / "candidate.diff").write_text("".join(diff))


def apply_candidate(output: Path) -> Path:
    output = output.resolve(strict=True)
    report = json.loads((output / "report.json").read_text())
    if report.get("status") not in ("success", "compile_only"):
        raise ValueError("only a validated candidate can be applied")
    record = read_record(output)
    validate_inventory(record, output)
    change = json.loads((output / "candidate.json").read_text())
    editable = record["config"]["editable"]
    root = Path(record["root"])
    target = root / editable
    if (
        Path(editable).is_absolute()
        or ".." in Path(editable).parts
        or str(target.absolute()) != change["target"]
        or change["editable"] != editable
        or target.is_symlink()
        or target.resolve() != target.absolute()
        or not target.resolve().is_relative_to(root)
    ):
        raise ValueError("candidate target path changed or contains a symlink")
    if change["base_sha256"] != record["sources"][editable]["sha256"]:
        raise ValueError("candidate base identity changed")
    for item in [*record["sources"].values(), *record.get("files", {}).values(), *record["inputs"]]:
        path = Path(item["path"])
        if path == target:
            continue
        if path.is_symlink() or not path.is_file() or digest(path) != item["sha256"]:
            raise ValueError(f"independent acceptance input changed: {path}")
    candidate = output / "repaired.cj"
    if candidate.is_symlink() or digest(candidate) != change["candidate_sha256"]:
        raise ValueError("candidate bytes changed since validation")

    # Stage before the final hash check, then replace the original directory entry atomically.
    def signature():
        info = target.stat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode

    before = signature()
    if digest(target) != change["base_sha256"]:
        raise ValueError("source changed since repair; rerun instead of overwriting user changes")
    descriptor, staged = tempfile.mkstemp(prefix=".inspect-cj-apply-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(candidate.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, stat.S_IMODE(before[-1]))
        if target.is_symlink() or signature() != before or digest(target) != change["base_sha256"]:
            raise ValueError("source changed while preparing application")
        os.replace(staged, target)
    finally:
        Path(staged).unlink(missing_ok=True)
    return target
