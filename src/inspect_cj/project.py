"""Explicit native build and executable test contracts."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import sysconfig
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from .config import load_config
from .process import CommandResult, run_command


def trusted_path(path: Path) -> Path:
    resolved = path.resolve()
    boundary = os.environ.get("INSPECT_CJ_WORKSPACE_ROOT")
    if boundary and not resolved.is_relative_to(Path(boundary).resolve(strict=True)):
        raise ValueError(f"Open and trust the containing workspace before accessing: {resolved}")
    return resolved


def positive(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 3600:
        raise ValueError(f"{name} must be a number in (0, 3600]")
    return float(value)


def strings(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) or "\0" in x for x in value):
        raise ValueError(f"{name} must be an array of strings without NUL")
    return value


@dataclass(frozen=True)
class TestCase:
    name: str
    args: list[str]
    stdin: str
    stdout: str | None
    exit_code: int = 0
    stderr: str | None = None
    comparison: str = "legacy_combined"


@dataclass(frozen=True)
class TestResult:
    name: str
    passed: bool
    actual_stdout: str
    expected_stdout: str
    returncode: int
    timed_out: bool
    actual_stderr: str = ""
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    truncated: bool = False
    launch: dict | None = None
    skipped: bool = False


def platform_sysroot() -> str | None:
    if platform.system() != "Darwin":
        return None
    xcrun = shutil.which("xcrun")
    if not xcrun:
        return None
    try:
        result = run_command(
            [xcrun, "--sdk", "macosx", "--show-sdk-path"], cwd=Path.home(), timeout=5
        )
    except OSError:
        return None
    path = result.output.strip()
    if (
        result.returncode == 0
        and not result.timed_out
        and Path(path).is_absolute()
        and Path(path).is_dir()
    ):
        return path
    return None


def debugger_framework() -> str | None:
    """CJDB 1.1 embeds Python 3.11, which may differ from our interpreter."""
    if sys.version_info[:2] == (3, 11) and sysconfig.get_config_var("PYTHONFRAMEWORK"):
        prefix = sysconfig.get_config_var("PYTHONFRAMEWORKPREFIX")
        if prefix and Path(prefix).is_absolute() and Path(prefix).is_dir():
            return prefix
    interpreter = shutil.which("python3.11")
    if not interpreter:
        return None
    program = (
        "import json,sys,sysconfig; "
        "print(json.dumps({'version':list(sys.version_info[:2]),"
        "'framework':sysconfig.get_config_var('PYTHONFRAMEWORKPREFIX') "
        "if sysconfig.get_config_var('PYTHONFRAMEWORK') else None}))"
    )
    try:
        result = run_command([interpreter, "-I", "-c", program], cwd=Path.home(), timeout=5)
        if result.returncode or result.timed_out:
            return None
        metadata = json.loads(result.output)
        prefix = metadata.get("framework")
        if (
            metadata.get("version") == [3, 11]
            and isinstance(prefix, str)
            and Path(prefix).is_absolute()
            and Path(prefix).is_dir()
        ):
            return prefix
    except (OSError, ValueError, AttributeError):
        pass
    return None


def tool_path(
    selected: str | None,
    candidates: list[Path],
    names: tuple[str, ...],
    label: str,
    *,
    required: bool = True,
) -> str | None:
    if selected:
        path = (
            Path(selected)
            if Path(selected).is_absolute()
            else Path(shutil.which(selected) or selected)
        )
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.absolute())
    else:
        for path in candidates:
            if path.is_file() and os.access(path, os.X_OK):
                return str(path.absolute())
        for name in names:
            found = shutil.which(name)
            if found:
                return str(Path(found).absolute())
    if required or selected:
        raise FileNotFoundError(
            f"native cjc/CJDB toolchain: {label} unavailable; install a compatible SDK and run "
            "inspect-cj configure --sdk PATH, or configure its explicit executable path"
        )
    return None


@dataclass(frozen=True)
class Toolchain:
    cjc: str
    cjdb: str
    runtime: str | None
    sysroot: str | None = None
    cjpm: str | None = None
    sdk: str | None = None
    python_framework: str | None = None

    @classmethod
    def discover(cls, sysroot: str | None = None) -> Toolchain:
        config = load_config()
        compiler_override = os.environ.get("CJC")
        sdk_setting = os.environ.get("CANGJIE_HOME") or (
            None if compiler_override else config.get("sdk")
        )
        selected_sdk = Path(sdk_setting) if sdk_setting else None
        cjc = tool_path(
            os.environ.get("CJC") or config.get("cjc"),
            [selected_sdk / "bin/cjc"] if selected_sdk else [],
            ("cjc",),
            "cjc",
        )
        # An explicit compiler determines its matching SDK unless a root was selected.
        sdk = selected_sdk or Path(cjc).resolve().parent.parent
        cjdb = tool_path(
            os.environ.get("CJDB") or config.get("cjdb"),
            [sdk / "third_party/llvm/bin/lldb", sdk / "bin/cjdb"],
            ("cjdb",),
            "CJDB",
        )
        cjpm = tool_path(
            os.environ.get("CJPM") or config.get("cjpm"),
            [sdk / "tools/bin/cjpm", sdk / "bin/cjpm"],
            ("cjpm",),
            "cjpm",
            required=False,
        )
        system = "darwin" if platform.system() == "Darwin" else "linux"
        arch = "aarch64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
        runtime = sdk / "runtime/lib" / f"{system}_{arch}_cjnative"
        framework = os.environ.get("CJDB_PYTHON_FRAMEWORK") or config.get("python_framework")
        if not framework and platform.system() == "Darwin":
            framework = debugger_framework()
        return cls(
            cjc,
            cjdb,
            str(runtime) if runtime.is_dir() else None,
            sysroot
            or os.environ.get("INSPECT_CJ_SYSROOT")
            or config.get("sysroot")
            or platform_sysroot(),
            cjpm,
            str(sdk),
            framework,
        )

    def environment(self, *, debugger: bool = False) -> dict[str, str]:
        env = dict(os.environ)
        sdk = Path(self.sdk) if self.sdk else Path(self.cjc).resolve().parent.parent
        env["CANGJIE_HOME"] = str(sdk)
        tool_paths = (sdk / "bin", sdk / "tools/bin", sdk / "third_party/llvm/bin")
        env["PATH"] = os.pathsep.join(
            [*(str(directory) for directory in tool_paths), env.get("PATH", "")]
        )
        if self.runtime:
            key = "DYLD_LIBRARY_PATH" if platform.system() == "Darwin" else "LD_LIBRARY_PATH"
            env[key] = self.runtime + (os.pathsep + env[key] if env.get(key) else "")
        if debugger and platform.system() == "Darwin":
            framework = os.environ.get("CJDB_PYTHON_FRAMEWORK") or self.python_framework
            if framework:
                env["DYLD_FRAMEWORK_PATH"] = framework
        return env


@dataclass(frozen=True)
class Project:
    root: Path
    sources: list[str]
    editable: str
    requirement: str
    tests: list[TestCase]
    flags: list[str] = field(default_factory=list)
    sysroot: str | None = None
    test_timeout: float = 10
    build_timeout: float = 60
    inputs: list[str] = field(default_factory=list)
    compile_only: bool = False
    native: dict | None = None
    files: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Project:
        path = trusted_path(path)
        project = cls.from_config(path.parent, json.loads(path.read_text()))
        return replace(project, inputs=[*project.inputs, str(path)])

    @classmethod
    def from_config(cls, root: Path, config: dict) -> Project:
        root = trusted_path(root)
        if not isinstance(config, dict):
            raise ValueError("project manifest must be an object")
        sources = strings(config.get("sources"), "sources")
        if not sources or len(set(sources)) != len(sources):
            raise ValueError("sources must be nonempty and unique")
        for source in sources:
            relative = Path(source)
            resolved = (root / relative).resolve()
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not resolved.is_relative_to(root)
                or relative.suffix != ".cj"
            ):
                raise ValueError("sources must be relative .cj files within the project")
            if not resolved.is_file() or resolved.stat().st_size > 1_000_000:
                raise ValueError(f"source missing or larger than 1MB: {source}")
        for name in strings(config.get("files", []), "files"):
            trusted_path(root / name)
        for name in strings(config.get("inputs", []), "inputs"):
            trusted_path(Path(name))
        editable = config.get("editable")
        requirement = config.get("requirement")
        if editable not in sources or not isinstance(requirement, str) or not requirement.strip():
            raise ValueError("editable must be a source; requirement must be nonempty text")
        cases = config.get("tests")
        native = config.get("native")
        if native is not None and not isinstance(native, dict):
            raise ValueError("native configuration must be an object")
        compile_only = config.get("compile_only", False)
        if type(compile_only) is not bool:
            raise ValueError("compile_only must be boolean")
        if (
            not isinstance(cases, list)
            or (not cases and not compile_only and native is None)
            or len(cases) > 1000
        ):
            raise ValueError("tests must contain 1 to 1000 executable cases")
        tests = []
        for case in cases:
            if not isinstance(case, dict):
                raise ValueError("test case must be an object")
            name, stdout, stdin = case.get("name"), case.get("stdout"), case.get("stdin", "")
            exit_code = case.get("exit_code", 0)
            comparison = case.get("comparison", "legacy_combined")
            stderr = case.get("stderr")
            if (
                not isinstance(name, str)
                or not name
                or comparison not in ("legacy_combined", "exact")
                or (not isinstance(stdout, str) and not (stdout is None and comparison == "exact"))
                or (stderr is not None and not isinstance(stderr, str))
                or not isinstance(stdin, str)
                or len((stdout or "").encode()) > 60000
                or len((stderr or "").encode()) > 60000
                or len(stdin.encode()) > 65536
                or type(exit_code) is not int
                or not 0 <= exit_code <= 255
            ):
                raise ValueError("invalid case name, text size or exit_code")
            tests.append(
                TestCase(
                    name,
                    strings(case.get("args", []), "args"),
                    stdin,
                    stdout,
                    exit_code,
                    stderr,
                    comparison,
                )
            )
        if len({case.name for case in tests}) != len(tests):
            raise ValueError("test names must be unique")
        sysroot = config.get("sysroot")
        if sysroot is not None and (not isinstance(sysroot, str) or not Path(sysroot).is_dir()):
            raise ValueError("sysroot must name an existing directory")
        return cls(
            root,
            sources,
            editable,
            requirement,
            tests,
            strings(config.get("cjc_flags", []), "cjc_flags"),
            sysroot,
            positive(config.get("test_timeout", 10), "test_timeout"),
            positive(config.get("build_timeout", 60), "build_timeout"),
            strings(config.get("inputs", []), "inputs"),
            compile_only,
            native,
            strings(config.get("files", []), "files"),
        )

    def snapshot(self, workspace: Path) -> Project:
        workspace.mkdir(parents=True, exist_ok=True)
        for name in self.files or self.sources:
            source = self.root / name
            if (
                Path(name).is_absolute()
                or ".." in Path(name).parts
                or source.is_symlink()
                or not source.resolve().is_relative_to(self.root.resolve())
            ):
                raise ValueError("snapshot paths must be relative ordinary files")
            target = workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return replace(self, root=workspace.resolve())

    def build(
        self, workspace: Path, tools: Toolchain | None = None, *, deadline: float | None = None
    ) -> tuple[Path, CommandResult]:
        tools = tools or Toolchain.discover(self.sysroot)
        if self.native:
            from .native import native_build

            return native_build(self, workspace, tools, deadline=deadline)
        workspace.mkdir(parents=True, exist_ok=True)
        executable = workspace.resolve() / "program"
        args = [tools.cjc, "-g"]
        if tools.sysroot:
            args += ["--sysroot", tools.sysroot]
        args += (
            self.flags
            + [str((self.root / s).resolve()) for s in self.sources]
            + ["-o", str(executable)]
        )
        result = run_command(
            args, cwd=self.root, timeout=self.build_timeout, env=tools.environment()
        )
        return executable, result


def run_suite(
    project: Project,
    executable: Path,
    tools: Toolchain | None = None,
    *,
    deadline: float | None = None,
) -> list[TestResult]:
    if project.native:
        from .native import native_suite

        return native_suite(
            project, executable, tools or Toolchain.discover(project.sysroot), deadline=deadline
        )
    results = []
    env = tools.environment() if tools else None
    for case in project.tests:
        remaining = deadline - time.monotonic() if deadline is not None else project.test_timeout
        if remaining <= 0:
            results.append(
                TestResult(case.name, False, "Run deadline exhausted", case.stdout, -1, True)
            )
            continue
        result = run_command(
            [str(executable), *case.args],
            cwd=project.root,
            input_text=case.stdin,
            timeout=min(project.test_timeout, remaining),
            env=env,
            separate_streams=case.comparison == "exact",
        )
        results.append(
            TestResult(
                case.name,
                not result.timed_out
                and result.returncode == case.exit_code
                and (
                    result.output == case.stdout
                    if case.comparison == "legacy_combined"
                    else (case.stdout is None or result.stdout == case.stdout)
                    and (case.stderr is None or result.stderr == case.stderr)
                    and not result.stdout_truncated
                    and not result.stderr_truncated
                ),
                result.output if case.comparison == "legacy_combined" else result.stdout,
                case.stdout,
                result.returncode,
                result.timed_out,
                result.stderr,
                result.stdout_bytes,
                result.stderr_bytes,
                result.stdout_truncated or result.stderr_truncated,
            )
        )
    return results
