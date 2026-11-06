"""Resolve user inputs into the existing immutable project contract."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path

from .project import Project, trusted_path


class NeedsInput(ValueError):
    def __init__(self, reason: str, missing: list[str], choices: list[str] | None = None):
        super().__init__(reason)
        self.missing = missing
        self.choices = choices or []


def new_output() -> Path:
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "inspect-cj/runs"
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    return cache.resolve() / uuid.uuid4().hex


def config_for(project: Project) -> dict:
    return {
        "sources": project.sources,
        "editable": project.editable,
        "requirement": project.requirement,
        "tests": [asdict(case) for case in project.tests],
        "cjc_flags": project.flags,
        "sysroot": project.sysroot,
        "build_timeout": project.build_timeout,
        "test_timeout": project.test_timeout,
        "inputs": project.inputs,
        "native": project.native,
        "files": project.files,
        "compile_only": project.compile_only,
    }


def bounded_text(path: Path, limit: int = 65536) -> str:
    with trusted_path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"input exceeds {limit} bytes: {path}")
    return data.decode("utf-8")


def resolve(args, *, task: str | None, stdin: str) -> Project:
    target = args.project
    if target is None:
        raise NeedsInput("Provide a source file, manifest or package directory", ["target"])
    from .native import find_manifest, resolve_package

    if target.suffix != ".json" and find_manifest(target):
        if any(
            (
                args.driver,
                args.stdin_file,
                args.program_args,
                args.expect_line is not None,
                args.expect_stdout is not None,
                args.expect_stderr is not None,
                args.expect_exit is not None,
                args.compile_only,
            )
        ):
            raise ValueError("native package assertions cannot be overridden by standalone options")
        return resolve_package(target, editable=args.editable, case=args.case, task=task)
    target = trusted_path(target).resolve(strict=True)
    if args.editable or args.case:
        raise ValueError("--editable and --case require a native cjpm target")
    if target.is_dir():
        if (target / "project.json").is_file():
            target = target / "project.json"
        else:
            sources = sorted(target.glob("*.cj"))
            if len(sources) != 1:
                raise NeedsInput(
                    "Select one implementation source explicitly",
                    ["target"],
                    [str(path) for path in sources],
                )
            target = sources[0].resolve()
    if target.suffix == ".json":
        if any(
            (
                task is not None,
                args.driver,
                args.stdin_file,
                args.program_args,
                args.expect_line is not None,
                args.expect_stdout is not None,
                args.expect_stderr is not None,
                args.expect_exit is not None,
                args.compile_only,
            )
        ):
            raise ValueError(
                "manifest acceptance cannot be overridden; edit or replay its contract"
            )
        return Project.load(target)
    if target.suffix != ".cj" or not target.is_file():
        raise ValueError("target must be a .cj file, project.json or supported directory")
    if args.expect_line is not None and args.expect_stdout is not None:
        raise ValueError("--expect-line and --expect-stdout are mutually exclusive")
    if not args.compile_only and all(
        value is None
        for value in (args.expect_line, args.expect_stdout, args.expect_stderr, args.expect_exit)
    ):
        raise NeedsInput(
            "Supply independent expected output/exit behavior or an assertion driver with "
            "--expect-exit 0; observed buggy output is never an oracle",
            ["expectation"],
        )
    sources = [target]
    if args.driver:
        driver = trusted_path(args.driver).resolve(strict=True)
        if driver == target or driver.suffix != ".cj":
            raise ValueError("--driver must be a different immutable .cj file")
        sources.append(driver)
    root = Path(os.path.commonpath([str(source.parent) for source in sources]))
    stdout = (
        "\n".join(args.expect_line) + "\n" if args.expect_line is not None else args.expect_stdout
    )
    config = {
        "sources": [str(source.relative_to(root)) for source in sources],
        "editable": str(target.relative_to(root)),
        "requirement": task
        or "Satisfy the independently supplied acceptance contract; "
        "preserve public signatures and all immutable driver assertions.",
        "tests": [
            {
                "name": "supplied",
                "args": args.program_args,
                "stdin": stdin,
                "stdout": stdout,
                "stderr": args.expect_stderr if args.expect_stderr is not None else "",
                "exit_code": args.expect_exit if args.expect_exit is not None else 0,
                "comparison": "exact",
            }
        ],
    }
    if args.compile_only:
        config["tests"] = []
        config["compile_only"] = True
    config["inputs"] = (
        [str(args.stdin_file.resolve())] if args.stdin_file and args.stdin_file != Path("-") else []
    )
    return Project.from_config(root, config)


def read_record(path: Path) -> dict:
    record = json.loads((path / "invocation.json").read_text())
    if record.get("schema_version") != 1:
        raise ValueError("unsupported invocation record version")
    return record
