"""Small CLI exposing the same native build, debugger and repair paths as the API."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .artifacts import apply_candidate, replay_project
from .config import KEYS, config_path, load_config, write_config
from .debugger import CJDBSession
from .jobs import NeedsInput, bounded_text, new_output, resolve
from .models import CodexModel, ResponsesModel, ScriptedModel, redact
from .native import UnsupportedWorkflow, find_manifest, resolve_package
from .process import run_command
from .project import Project, TestCase, TestResult, Toolchain, run_suite
from .repair import RepairController


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def doctor(sysroot: str | None) -> int:
    tools = Toolchain.discover(sysroot)
    with tempfile.TemporaryDirectory(prefix="inspect-cj-doctor-") as directory:
        # CJDB matches canonical debug paths, including macOS /var -> /private/var.
        root = Path(directory).resolve()
        compiler = run_command([tools.cjc, "--version"], cwd=root, timeout=10)
        (root / "probe.cj").write_text(
            "main() {\n    var probe: Int64 = 6\n    println(probe)\n    return 0\n}\n"
        )
        project = Project(
            root,
            ["probe.cj"],
            "probe.cj",
            "Print the variable",
            [TestCase("probe", [], "", "6\n")],
            sysroot=sysroot,
        )
        executable, build = project.build(root / "build", tools)
        if build.returncode or build.timed_out:
            emit(
                {
                    "status": "failed",
                    "compiler": compiler.output,
                    "build": asdict(build),
                    "sysroot": tools.sysroot,
                    "hint": (
                        "If the Apple SDK is incompatible with this Cangjie SDK, persist a "
                        "compatible SDK with inspect-cj configure --sysroot PATH "
                        "(or use doctor --sysroot PATH). Automatic Apple SDK discovery "
                        "does not guarantee compiler compatibility."
                    ),
                }
            )
            return 2
        with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
            observations = [
                session.execute(action).to_dict()
                for action in (
                    {"action": "set_breakpoint", "line": 3},
                    {"action": "control_execution", "command": "continue"},
                    {"action": "inspect", "variable": "probe"},
                    {"action": "interact_code", "code": "set probe = 10"},
                    {"action": "inspect", "variable": "probe"},
                    {"action": "interact_code", "code": "expression probe + 1"},
                    {"action": "control_execution", "command": "continue"},
                )
            ]
            proven = all(observations[i]["status"] == "ok" for i in (0, 1, 2))
            emit(
                {
                    "status": "success" if proven else "failed",
                    "compiler": compiler.output,
                    "debugger": session.version,
                    "cjc_path": tools.cjc,
                    "cjdb_path": tools.cjdb,
                    "sysroot": tools.sysroot,
                    "capabilities": session.capabilities,
                    "observations": observations,
                }
            )
            return 0 if proven else 2


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        emit({"status": "failed", "reason": message})
        raise SystemExit(2)


def parser() -> argparse.ArgumentParser:
    root = JsonArgumentParser(prog="inspect-cj", description="Native CJDB-assisted Cangjie repair")
    try:
        installed_version = version("inspectcoder-cj")
    except PackageNotFoundError:
        installed_version = "uninstalled source"
    root.add_argument("--version", action="version", version=f"%(prog)s {installed_version}")
    commands = root.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure", help="show or persist user toolchain paths")
    for key in KEYS:
        configure.add_argument("--" + key.replace("_", "-"), help=f"absolute {key} path")
    probe = commands.add_parser(
        "doctor", help="prove native breakpoints, inspection and capabilities"
    )
    probe.add_argument("--sysroot", help="explicit compiler SDK root; no system setting is changed")
    for name, help_text in (
        ("test", "compile and run every configured executable test"),
        ("debug", "persistent CJDB action/observation JSONL session"),
        ("repair", "inspect, propose an evidence plan, patch and validate"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "project", type=Path, nargs="?", help="source, directory or project.json"
        )
        if name == "debug":
            command.add_argument("--case", help="test name; defaults to the first case")
            command.add_argument("--actions", type=Path, help="JSONL file; defaults to stdin")
            command.add_argument("--timeout", type=float, default=10)
        if name == "repair":
            command.add_argument(
                "--compile-only",
                action="store_true",
                help="compile without claiming semantic validation",
            )
            command.add_argument(
                "--output", type=Path, help="new artifact directory; defaults to private user cache"
            )
            command.add_argument(
                "--replay", type=Path, help="revalidate and replay a run directory"
            )
            command.add_argument("--case", help="opaque native case identity from test results")
            command.add_argument("--editable", help="one implementation source relative to package")
            command.add_argument("--task", help="intended behavior, or - to read stdin")
            command.add_argument(
                "--driver", type=Path, help="immutable assertion/reproducer driver"
            )
            command.add_argument("--stdin-file", type=Path, help="program input, or - for stdin")
            command.add_argument("--expect-line", action="append", help="exact line plus newline")
            command.add_argument("--expect-stdout", help="exact stdout including newlines")
            command.add_argument("--expect-stderr", help="exact stderr; new cases default to empty")
            command.add_argument("--expect-exit", type=int, help="explicit expected process exit")
            command.add_argument(
                "--non-interactive", action="store_true", help="never ask questions"
            )
            command.add_argument(
                "--transport", choices=("codex", "responses", "scripted"), default="codex"
            )
            command.add_argument("--model", help="model override; required for Responses")
            command.add_argument("--script", type=Path, help="offline role/response JSON array")
            command.add_argument("--max-attempts", type=int, default=5)
            command.add_argument("--max-steps", type=int, default=20)
            command.add_argument("--run-timeout", type=float, default=600)
            command.add_argument("--model-timeout", type=float, default=120)
            command.add_argument("--debug-timeout", type=float, default=10)
    driver = commands.add_parser("driver", help="draft a scratch driver from a supplied expression")
    driver.add_argument("--expression", required=True, help="Cangjie expression supplied by you")
    driver.add_argument("--expect-line", help="independent expected line; never inferred")
    driver.add_argument("--output", type=Path, required=True, help="new scratch .cj file")
    apply = commands.add_parser(
        "apply", help="explicitly apply a validated candidate with hash guards"
    )
    apply.add_argument("run", type=Path)
    return root


class NativeDebugUnavailable(RuntimeError):
    def __init__(self, reason: str, results: list[TestResult], *, timed_out: bool = False):
        super().__init__(reason)
        self.status = "timeout" if timed_out else "incomplete"
        self.exit_code = 4 if timed_out else 3
        self.results = results


def native_debug_launch(
    results: list[TestResult], selected: str | None
) -> tuple[TestCase, Path, dict]:
    if any(case.timed_out for case in results):
        raise NativeDebugUnavailable(
            "native discovery or suite execution timed out; debugger was not started",
            results,
            timed_out=True,
        )
    if not results or any(case.launch is None for case in results):
        raise NativeDebugUnavailable(
            "native suite is incomplete; no complete case launch metadata", results
        )
    focused = (
        next((case for case in results if case.name == selected), None)
        if selected
        else next((case for case in results if not case.passed), results[0])
    )
    if focused is None:
        raise ValueError(f"native case {selected!r} was not discovered")
    launch = focused.launch
    if (
        not isinstance(launch, dict)
        or not isinstance(launch.get("executable"), str)
        or not launch["executable"]
        or not isinstance(launch.get("args"), list)
        or any(not isinstance(arg, str) for arg in launch["args"])
    ):
        raise NativeDebugUnavailable("native case launch metadata is incomplete", results)
    return TestCase(focused.name, launch["args"], "", None), Path(launch["executable"]), launch


def debug(project: Project, executable: Path, tools: Toolchain, args: argparse.Namespace) -> int:
    launch = None
    if project.native:
        try:
            case, executable, launch = native_debug_launch(
                run_suite(project, executable, tools), args.case
            )
        except NativeDebugUnavailable as error:
            emit(
                {
                    "status": error.status,
                    "reason": str(error),
                    "tests": [asdict(case) for case in error.results],
                }
            )
            return error.exit_code
    else:
        case = (
            next((case for case in project.tests if case.name == args.case), None)
            if args.case
            else project.tests[0]
        )
    if case is None:
        raise ValueError(f"test case {args.case!r} is not configured")
    with CJDBSession(
        project, executable, case, tools=tools, timeout=args.timeout, launch=launch
    ) as session:
        emit({"type": "session", **session.snapshot()})
        stream = args.actions.open() if args.actions else sys.stdin
        exit_code = 0
        try:
            while line := stream.readline(65537):
                if len(line) > 65536:
                    raise ValueError("JSONL action exceeds 64KiB")
                if not line.strip():
                    continue
                try:
                    observation = session.execute(json.loads(line))
                    emit(observation.to_dict())
                    if observation.status != "ok":
                        exit_code = 3 if observation.status == "unsupported" else 2
                except json.JSONDecodeError:
                    emit({"status": "invalid", "reason": "action must be one JSON object per line"})
                    exit_code = 2
        finally:
            if args.actions:
                stream.close()
        return exit_code


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    program_args = []
    if argv and argv[0] == "repair" and "--" in argv:
        separator = argv.index("--")
        argv, program_args = argv[:separator], argv[separator + 1 :]
    args = parser().parse_args(argv)
    args.program_args = program_args
    try:
        if args.command == "configure":
            updates = {key: getattr(args, key) for key in KEYS if getattr(args, key) is not None}
            path, values = write_config(updates) if updates else (config_path(), load_config())
            emit({"status": "success", "path": str(path), "config": values})
            return 0
        if args.command == "driver":
            if args.expect_line is None:
                raise NeedsInput("An independent expected line is required", ["expectation"])
            if args.output.suffix != ".cj" or len(args.expression.encode()) > 65536:
                raise ValueError("driver output must be .cj; expression must fit 64KiB")
            source = (
                "main(args: Array<String>) {\n    println(" + args.expression + ");\n    0\n}\n"
            )
            with args.output.open("x") as stream:
                stream.write(source)
            emit(
                {
                    "status": "success",
                    "driver": str(args.output.resolve()),
                    "expect_line": args.expect_line,
                    "next": [
                        "repair",
                        "IMPLEMENTATION.cj",
                        "--driver",
                        str(args.output.resolve()),
                        "--expect-line",
                        args.expect_line,
                    ],
                }
            )
            return 0
        if args.command == "apply":
            target = apply_candidate(args.run)
            emit({"status": "success", "applied": str(target)})
            return 0
        if args.command == "doctor":
            return doctor(args.sysroot)
        if args.command == "repair":
            if args.task == "-" and args.stdin_file == Path("-"):
                raise ValueError("--task - and --stdin-file - are mutually exclusive")
            task = args.task
            if task == "-":
                task = sys.stdin.read(65537)
                if len(task.encode()) > 65536:
                    raise ValueError("task exceeds 64KiB")
            stdin = ""
            if args.stdin_file:
                stdin = (
                    sys.stdin.read(65537)
                    if args.stdin_file == Path("-")
                    else bounded_text(args.stdin_file)
                )
                if len(stdin.encode()) > 65536:
                    raise ValueError("program stdin exceeds 64KiB")
            if args.replay:
                if args.project or any(
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
                        args.case,
                        args.editable,
                    )
                ):
                    raise ValueError("--replay cannot override recorded inputs")
                project = replay_project(args.replay)
            else:
                project = resolve(args, task=task, stdin=stdin)
            args.output = args.output or new_output()
        else:
            if args.project is None:
                raise ValueError("project.json is required")
            if args.project.suffix != ".json" and find_manifest(args.project):
                project = resolve_package(
                    args.project, require_editable=False, case=getattr(args, "case", None)
                )
            else:
                project = Project.load(args.project)
        if args.command == "debug" and not project.native and not project.tests:
            raise NeedsInput("Debugging requires a declared executable test case", ["case"])
        tools = Toolchain.discover(project.sysroot)
        if args.command == "repair":
            if args.transport == "scripted":
                if not args.script:
                    raise ValueError("scripted transport requires --script")
                model = ScriptedModel.load(args.script)
            elif args.transport == "responses":
                model = ResponsesModel(args.model)
            else:
                model = CodexModel(args.model)
            controller = RepairController(
                project,
                model,
                args.output,
                tools=tools,
                max_attempts=args.max_attempts,
                max_steps=args.max_steps,
                run_timeout=args.run_timeout,
                model_timeout=args.model_timeout,
                debug_timeout=args.debug_timeout,
            )
            result = controller.run()
            emit(asdict(result) | {"transport": model.label})
            return {
                "success": 0,
                "compile_only": 0,
                "interrupted": 130,
                "exhausted": 4,
                "unsupported": 3,
                "failed": 2,
                "needs_input": 5,
            }[result.status]
        with tempfile.TemporaryDirectory(prefix="inspect-cj-build-") as directory:
            if project.native:
                project = project.snapshot(Path(directory) / "workspace")
            executable, build = project.build(Path(directory) / "build", tools)
            if build.returncode or build.timed_out:
                emit({"status": "failed", "build": asdict(build)})
                return 2
            if args.command == "debug":
                return debug(project, executable, tools, args)
            tests = run_suite(project, executable, tools)
            passed = all(test.passed for test in tests)
            status = "compile_only" if project.compile_only else "success"
            emit({"status": status if passed else "failed", "tests": [asdict(t) for t in tests]})
            return 0 if passed else 1
    except UnsupportedWorkflow as error:
        emit({"status": "unsupported", "reason": str(error)})
        return 3
    except NeedsInput as error:
        emit(
            {
                "status": "needs_input",
                "reason": str(error),
                "missing": error.missing,
                "choices": error.choices,
            }
        )
        return 5
    except FileNotFoundError as error:
        # Missing project files are ordinary CLI errors; missing toolchains are unsupported.
        status = "unsupported" if "cjc/CJDB" in str(error) else "failed"
        emit({"status": status, "reason": redact(str(error))})
        return 3 if status == "unsupported" else 2
    except (OSError, ValueError, RuntimeError) as error:
        emit({"status": "failed", "reason": redact(str(error))})
        return 2
    except KeyboardInterrupt:
        emit({"status": "failed", "reason": "interrupted; owned subprocesses cleaned up"})
        return 130
