"""Version-tested cjpm builds, native assertion acceptance and exact case launches."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import stat
import sys
import time
import tomllib
import uuid
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from .jobs import NeedsInput
from .process import CommandResult, run_command
from .project import Project, TestResult, Toolchain, trusted_path


class UnsupportedWorkflow(RuntimeError):
    pass


def _manifest_entry(path: Path) -> Path | None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(mode):
        raise UnsupportedWorkflow(f"native manifest does not follow symlinks: {path}")
    if not stat.S_ISREG(mode):
        raise UnsupportedWorkflow(f"native manifest requires an ordinary file: {path}")
    return trusted_path(path)


def find_manifest(target: Path) -> Path | None:
    if target.name == "cjpm.toml":
        manifest = _manifest_entry(target)
        if manifest is not None:
            return manifest
    target = trusted_path(target)
    directory = target if target.is_dir() else target.parent
    for parent in (directory, *directory.parents):
        manifest = _manifest_entry(parent / "cjpm.toml")
        if manifest is not None:
            return manifest
        if (parent / ".git").exists():
            break
    return None


def relative_path(root: Path, value: str, *, owner: Path | None = None) -> Path:
    if Path(value).is_absolute():
        raise UnsupportedWorkflow(f"native manifest paths must be relative: {value}")
    path = (owner or root) / value
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise UnsupportedWorkflow(f"native path escapes its isolated root: {value}")
    return path


def validate_manifest_paths(root: Path, owner: Path, metadata: dict) -> None:
    workspace = metadata.get("workspace", {})
    for key in ("members", "build-members", "test-members"):
        for value in workspace.get(key, []):
            relative_path(root, value, owner=owner)
    for section in (metadata.get("package", {}), workspace):
        for key in ("src-dir", "target-dir"):
            if section.get(key):
                relative_path(root, section[key], owner=owner)

    def validate_paths(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "path" and isinstance(child, str):
                    resolved = relative_path(root, child, owner=owner)
                    if not resolved.exists():
                        raise UnsupportedWorkflow(f"native dependency/resource missing: {child}")
                else:
                    validate_paths(child)
        elif isinstance(value, list):
            for child in value:
                validate_paths(child)

    validate_paths(metadata)


def inventory(root: Path, *, ignored_roots: tuple[Path, ...] = ()) -> list[str]:
    root = root.resolve(strict=True)
    excluded = {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".inspect-cj",
    }
    files, total = [], 0
    outputs = set()
    for parent, directories, names in os.walk(root, followlinks=False):
        # Validate entries before even parsing a nested manifest's output directory.
        for name in [*directories, *names]:
            path = Path(parent) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise UnsupportedWorkflow(f"native snapshot does not follow symlinks: {path}")
            if not path.resolve().is_relative_to(root):
                raise UnsupportedWorkflow(f"native snapshot path escapes its isolated root: {path}")
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise UnsupportedWorkflow(f"native snapshot requires ordinary entries: {path}")
        manifest = Path(parent) / "cjpm.toml"
        if manifest.is_file():
            metadata = tomllib.loads(manifest.read_text())
            validate_manifest_paths(root, Path(parent), metadata)
            section = metadata.get("package", metadata.get("workspace", {}))
            outputs.add((Path(parent) / (section.get("target-dir") or "target")).resolve())
        directories[:] = sorted(
            name
            for name in directories
            if name not in excluded
            and (Path(parent) / name).resolve() not in outputs
            and (Path(parent) / name).resolve() not in ignored_roots
        )
        for name in sorted(names):
            path = Path(parent) / name
            if not path.is_file():
                raise UnsupportedWorkflow(f"native snapshot requires ordinary files: {path}")
            total += path.stat().st_size
            files.append(str(path.relative_to(root)))
            if len(files) > 10000 or total > 128 * 1024 * 1024:
                raise UnsupportedWorkflow("native snapshot exceeds 10000 files or 128MiB")
    return sorted(files)


def resolve_package(
    target: Path,
    *,
    editable: str | None = None,
    case: str | None = None,
    task: str | None = None,
    require_editable: bool = True,
) -> Project:
    manifest = find_manifest(target)
    target = trusted_path(target).resolve(strict=True)
    if manifest is None:
        raise ValueError("no cjpm.toml within the selected repository boundary")
    root = manifest.parent
    # A declared containing workspace owns its member build/test semantics.
    for parent in root.parents:
        candidate = _manifest_entry(parent / "cjpm.toml")
        if candidate is not None:
            config = tomllib.loads(candidate.read_text())
            members = config.get("workspace", {}).get("members", [])
            if root in [relative_path(parent, name).resolve() for name in members]:
                root, manifest = parent, candidate
                break
        if (parent / ".git").exists():
            break
    config = tomllib.loads(manifest.read_text())
    validate_manifest_paths(root, root, config)
    workspace = config.get("workspace")
    if workspace is not None:
        members = workspace.get("members", [])
        if not isinstance(members, list) or not members:
            raise UnsupportedWorkflow("native workspace must declare its member modules")
        modules = [relative_path(root, name) for name in members]
    elif isinstance(config.get("package"), dict):
        modules = [root]
    else:
        raise ValueError("cjpm.toml requires package or workspace metadata")
    files = inventory(root)
    sources = [name for name in files if name.endswith(".cj")]
    implementations = []
    for module in modules:
        metadata = tomllib.loads((module / "cjpm.toml").read_text())
        src = relative_path(root, metadata["package"].get("src-dir", "src"), owner=module)
        implementations.extend(
            name
            for name in sources
            if (root / name).is_relative_to(src)
            and not name.endswith("_test.cj")
            and Path(name).name != "build.cj"
        )

    if target.suffix == ".cj":
        if editable and (root / editable).resolve() != target:
            raise ValueError("source target conflicts with --editable")
        editable = str(target.relative_to(root))
    if editable is None:
        if len(implementations) != 1 and require_editable:
            raise NeedsInput(
                "Select one editable implementation file", ["editable"], sorted(implementations)
            )
        editable = next(iter(sorted(implementations or sources)), None)
    if not editable or editable not in sources:
        raise ValueError("--editable must name one .cj source relative to the cjpm root")
    if require_editable and (
        editable.endswith("_test.cj")
        or re.search(
            r"@(Test|TestCase|BeforeAll|BeforeEach|AfterAll|AfterEach)\b",
            (root / editable).read_text(),
        )
    ):
        raise UnsupportedWorkflow("editable native implementations must be separate from test code")
    cjpm = Toolchain.discover().cjpm
    if not cjpm:
        raise UnsupportedWorkflow("matching cjpm unavailable; run inspect-cj configure --cjpm PATH")
    return replace(
        Project.from_config(
            root,
            {
                "sources": sources,
                "editable": editable,
                "requirement": task
                or "Satisfy every immutable native assertion and fixture; preserve APIs.",
                # Native assertions, not synthetic stdout cases, supply semantic acceptance.
                "tests": [],
                "compile_only": True,
            },
        ),
        native={"cjpm": str(cjpm), "case": case},
        files=files,
        compile_only=False,
    )


def remaining(deadline: float | None, limit: float) -> float:
    value = min(limit, deadline - time.monotonic()) if deadline is not None else limit
    if value <= 0:
        raise RuntimeError("shared run deadline exhausted")
    return value


def environment(project: Project, tools: Toolchain, build: Path) -> dict[str, str]:
    env = tools.environment()
    if tools.sysroot:
        shim = build / "compiler-shim"
        shim.mkdir(parents=True, exist_ok=True, mode=0o700)
        wrapper = shim / "cjc"
        program = (
            "import os, sys\n"
            f"compiler = {str(Path(tools.cjc).resolve())!r}\nargs = sys.argv[1:]\n"
            "if not any(a == '--sysroot' or a.startswith('--sysroot=') for a in args):\n"
            f"    args = ['--sysroot', {tools.sysroot!r}, *args]\n"
            "os.execv(compiler, [compiler, *args])\n"
        )
        wrapper.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(program)} "$@"\n'
        )
        wrapper.chmod(0o700)
        env["PATH"] = str(shim) + os.pathsep + env.get("PATH", "")
    return env


def command(
    project: Project,
    tools: Toolchain,
    build: Path,
    args: list[str],
    *,
    deadline: float | None,
    limit: float,
) -> CommandResult:
    return run_command(
        [project.native["cjpm"], *args],
        cwd=project.root,
        timeout=remaining(deadline, limit),
        env=environment(project, tools, build),
        output_limit=262144,
    )


def native_build(
    project: Project, build: Path, tools: Toolchain, *, deadline: float | None = None
) -> tuple[Path, CommandResult]:
    build = build.resolve()
    build.mkdir(parents=True, exist_ok=True)
    if platform.system() != "Darwin" or platform.machine() not in ("arm64", "aarch64"):
        raise UnsupportedWorkflow("native cjpm adapter is certified on macOS arm64 SDK 1.1.0 only")
    versions = {}
    for name, executable in (("cjc", tools.cjc), ("cjpm", project.native["cjpm"])):
        result = run_command(
            [executable, "--version"],
            cwd=project.root,
            timeout=remaining(deadline, project.build_timeout),
            env=environment(project, tools, build),
        )
        if result.returncode or result.timed_out or not re.search(r"\b1\.1\.0\b", result.output):
            raise UnsupportedWorkflow(f"native adapter requires matching SDK/cjpm 1.1.0: {name}")
        versions[name] = result.output
    (build / "toolchain.json").write_text(json.dumps(versions, indent=2) + "\n")
    result = command(
        project,
        tools,
        build,
        ["test", "-g", "--no-run", "--target-dir", str(build / "target"), "--no-color", "-V"],
        deadline=deadline,
        limit=project.build_timeout,
    )
    (build / "build.log").write_text(result.output)
    return build, result


def opaque_case(module: str, package: str, classname: str, name: str) -> str:
    identity = json.dumps([module, package, classname, name], ensure_ascii=False).encode()
    return "cjpm:" + hashlib.sha256(identity).hexdigest()[:32]


def read_xml_cases(reports: Path):
    count = 0
    for path in sorted(reports.rglob("*.xml")):
        if path.stat().st_size > 2_000_000:
            raise UnsupportedWorkflow("native XML report exceeds 2MB")
        try:
            suite = ET.fromstring(path.read_bytes())
            cases = suite.findall("testcase")
            if suite.tag != "testsuite" or int(suite.get("tests", "-1")) != len(cases):
                raise UnsupportedWorkflow("native XML case count does not match its summary")
        except (ET.ParseError, ValueError) as error:
            raise UnsupportedWorkflow("native XML report is malformed") from error
        count += len(cases)
        if count > 1000:
            raise UnsupportedWorkflow("native suite exceeds 1000 cases")
        yield from cases


def native_suite(
    project: Project, build: Path, tools: Toolchain, *, deadline: float | None = None
) -> list[TestResult]:
    discovery = build / ("discovery-" + uuid.uuid4().hex)
    discovered = command(
        project,
        tools,
        build,
        [
            "test",
            "-g",
            "--skip-build",
            "--dry-run",
            "--target-dir",
            str(build / "target"),
            "--no-color",
            "--no-progress",
            "--report-format",
            "xml",
            "--report-path",
            str(discovery),
        ],
        deadline=deadline,
        limit=project.test_timeout,
    )
    (build / "discovery.log").write_text(discovered.output)
    if discovered.timed_out:
        return [
            TestResult(
                "native-discovery", False, discovered.output, "native discovery completes", -1, True
            )
        ]
    if discovered.returncode:
        raise UnsupportedWorkflow("native case discovery failed")
    expected = {(case.get("classname"), case.get("name")) for case in read_xml_cases(discovery)}
    if not expected:
        raise UnsupportedWorkflow("native discovery found no test cases")
    reports = build / ("suite-" + uuid.uuid4().hex)
    result = command(
        project,
        tools,
        build,
        [
            "test",
            "-g",
            "--skip-build",
            "--target-dir",
            str(build / "target"),
            "--no-color",
            "--no-progress",
            "--report-format",
            "xml",
            "--report-path",
            str(reports),
            "-V",
        ],
        deadline=deadline,
        limit=project.test_timeout,
    )
    (build / "suite.log").write_text(result.output)
    if result.timed_out:
        return [
            TestResult("native-suite", False, result.output, "native suite completes", -1, True)
        ]
    matches = re.findall(r"with input: (\{[^\n]+\})", result.output)
    if len(matches) != 1:
        raise UnsupportedWorkflow("cjpm did not expose one supported native runner launch contract")
    envelope = json.loads(matches[0])
    if envelope.get("apiVersion") != 2:
        raise UnsupportedWorkflow("unsupported cjpm native runner protocol")
    launches = {}
    for module in envelope["testModules"]:
        for package in module["testPackages"]:
            launch = package["executeCommand"]
            executable = Path(launch["command"])
            if not executable.resolve().is_relative_to(build) or not executable.is_file():
                raise UnsupportedWorkflow("native test executable escapes the private build")
            launches[package["name"]] = (module["name"], launch)
    cases = []
    reported = set()
    for case in read_xml_cases(reports):
        reported.add((case.get("classname"), case.get("name")))
        classname, name = case.get("classname", ""), case.get("name", "")
        packages = [package for package in launches if classname.startswith(package + ".")]
        if not packages or not name:
            raise UnsupportedWorkflow("native XML case cannot be mapped to its test executable")
        package = max(packages, key=len)
        module, launch = launches[package]
        native_class = classname[len(package) + 1 :]
        if not re.fullmatch(r"[A-Za-z_][\w.]*", native_class + "." + name):
            raise UnsupportedWorkflow("native case name cannot be selected exactly by this runner")
        case_id = opaque_case(module, package, classname, name)
        failures = case.findall("failure") + case.findall("error")
        skipped = case.find("skipped") is not None
        args = [
            arg
            for arg in launch.get("args", [])
            if not arg.startswith(("--report-path=", "--report-format="))
        ]
        focused = {
            "executable": launch["command"],
            "executable_sha256": hashlib.sha256(Path(launch["command"]).read_bytes()).hexdigest(),
            "args": [f"--filter={native_class}.{name}", *args],
            "cwd": str(project.root),
            "environment": launch.get("env", {}),
            "module": module,
            "package": package,
            "suite": native_class,
            "case": name,
        }
        cases.append(
            TestResult(
                case_id,
                not failures and not skipped,
                "\n".join(item.text or "" for item in failures),
                "native assertions pass",
                1 if failures else 0,
                False,
                launch=focused,
                skipped=skipped,
            )
        )
    if reported != expected:
        raise UnsupportedWorkflow("executed native suite does not match discovered case membership")
    if not cases or len({case.name for case in cases}) != len(cases):
        raise UnsupportedWorkflow("native suite produced no cases or duplicate case identities")
    if result.returncode != 0 and all(case.passed for case in cases):
        raise UnsupportedWorkflow(
            "native runner failed despite its case reports; full suite inconclusive"
        )
    selected = project.native.get("case")
    if selected:
        found = next((case for case in cases if case.name == selected), None)
        if found is None:
            raise NeedsInput(
                "Selected native case was not discovered",
                ["case"],
                sorted(case.name for case in cases),
            )
    return sorted(cases, key=lambda case: (case.name != selected, case.name))
