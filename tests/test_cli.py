import json
import os
import sys
from pathlib import Path

import pytest

from inspect_cj.process import run_command


def cli(args, tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return run_command(
        [sys.executable, "-m", "inspect_cj", *args], cwd=tmp_path, timeout=5, env=env
    )


def test_cli_help_describes_subcommands(tmp_path):
    result = cli(["--help"], tmp_path)
    assert result.returncode == 0
    for name in ("doctor", "test", "debug", "repair"):
        assert name in result.output


def test_cli_missing_project_is_json_error_without_traceback(tmp_path):
    result = cli(["test", str(tmp_path / "absent.json")], tmp_path)
    assert result.returncode == 2
    assert json.loads(result.output)["status"] == "failed"
    assert "Traceback" not in result.output


@pytest.mark.native
@pytest.mark.skipif(os.environ.get("INSPECT_CJ_NATIVE") != "1", reason="requires native CJDB")
def test_native_doctor_resolves_symlinked_temporary_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    env = dict(os.environ, TMPDIR=str(alias))
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = run_command(
        [sys.executable, "-m", "inspect_cj", "doctor"], cwd=tmp_path, timeout=30, env=env
    )
    assert result.returncode == 0, result.output
    report = json.loads(result.output)
    assert report["status"] == "success"
    observations = report["observations"]
    assert observations[1]["state"] == "RuntimeState"
    assert "probe = 6" in observations[2]["output"]
    assert observations[3]["status"] in {"ok", "unsupported"}
    assert observations[5]["status"] in {"ok", "unsupported"}
    assert observations[-1]["state"] == "Done"
    assert observations[-1]["output"] == ("10\n" if observations[3]["status"] == "ok" else "6\n")


def test_cli_rejects_endpoint_override(tmp_path):
    result = cli(
        ["repair", "project.json", "--output", "result", "--endpoint", "http://localhost"],
        tmp_path,
    )
    assert result.returncode == 2
    assert "unrecognized arguments: --endpoint" in result.output


@pytest.mark.native
@pytest.mark.skipif(os.environ.get("INSPECT_CJ_NATIVE") != "1", reason="requires native CJDB")
def test_native_debug_preserves_large_environment_protocol_message(tmp_path):
    source = tmp_path / "main.cj"
    source.write_text("main() {\n    let value: Int64 = 42\n    println(value)\n    0\n}\n")
    project = tmp_path / "project.json"
    project.write_text(
        json.dumps(
            {
                "sources": ["main.cj"],
                "editable": "main.cj",
                "requirement": "Print 42",
                "tests": [{"name": "probe", "stdout": "42\n"}],
            }
        )
    )
    actions = tmp_path / "actions.jsonl"
    actions.write_text(
        "\n".join(
            json.dumps(action)
            for action in [
                {"action": "set_breakpoint", "line": 3},
                {"action": "control_execution", "command": "continue"},
                {"action": "inspect", "variable": "value"},
                {"action": "control_execution", "command": "continue"},
            ]
        )
        + "\n"
    )
    env = dict(os.environ, INSPECT_CJ_PROTOCOL_REGRESSION="x" * 65536)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = run_command(
        [
            sys.executable,
            "-m",
            "inspect_cj",
            "debug",
            str(project),
            "--actions",
            str(actions),
            "--timeout",
            "3",
        ],
        cwd=tmp_path,
        timeout=25,
        env=env,
    )
    assert result.returncode == 0, result.output
    observations = [json.loads(line) for line in result.output.splitlines()]
    assert observations[2]["state"] == "RuntimeState"
    assert "value = 42" in observations[3]["output"]
    assert observations[-1]["state"] == "Done"
    assert (
        source.read_text() == "main() {\n    let value: Int64 = 42\n    println(value)\n    0\n}\n"
    )


@pytest.mark.native
@pytest.mark.skipif(os.environ.get("INSPECT_CJ_NATIVE") != "1", reason="requires native CJDB")
def test_native_debug_preserves_large_stack_protocol_response(tmp_path):
    root = tmp_path / ("long-source-directory-" * 10)
    root.mkdir()
    source = root / "main.cj"
    source.write_text(
        "func descend(depth: Int64): Int64 {\n"
        "    if (depth == 0) {\n"
        "        let value: Int64 = 42\n"
        "        return value\n"
        "    }\n"
        "    return descend(depth - 1)\n"
        "}\n"
        "main() { println(descend(48)); 0 }\n"
    )
    project = root / "project.json"
    project.write_text(
        json.dumps(
            {
                "sources": ["main.cj"],
                "editable": "main.cj",
                "requirement": "Print 42",
                "tests": [{"name": "probe", "stdout": "42\n"}],
            }
        )
    )
    actions = root / "actions.jsonl"
    actions.write_text(
        "\n".join(
            json.dumps(action)
            for action in [
                {"action": "set_breakpoint", "line": 4},
                {"action": "control_execution", "command": "continue"},
                {"action": "inspect", "variable": "value"},
                {"action": "control_execution", "command": "continue"},
            ]
        )
        + "\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = run_command(
        [
            sys.executable,
            "-m",
            "inspect_cj",
            "debug",
            str(project),
            "--actions",
            str(actions),
            "--timeout",
            "3",
        ],
        cwd=root,
        timeout=25,
        env=env,
        output_limit=100000,
    )
    assert result.returncode == 0, result.output
    observations = [json.loads(line) for line in result.output.splitlines()]
    assert len(observations[2]["frames"]) == 32
    assert len(json.dumps(observations[2])) > 8192
    assert "value = 42" in observations[3]["output"]
    assert observations[-1]["state"] == "Done"


@pytest.mark.parametrize(
    "name,timed_out", [("native-discovery", True), ("native-suite", True), ("native-suite", False)]
)
def test_debug_rejects_phase_summaries_before_starting_cjdb(monkeypatch, capsys, name, timed_out):
    from types import SimpleNamespace

    from inspect_cj import cli as command
    from inspect_cj.project import TestResult as Result

    monkeypatch.setattr(
        command,
        "run_suite",
        lambda *a: [Result(name, False, "phase failed", "native suite completes", -1, timed_out)],
    )

    def forbidden_session(*args, **kwargs):
        pytest.fail("a phase summary must not start CJDB")

    monkeypatch.setattr(command, "CJDBSession", forbidden_session)
    code = command.debug(
        SimpleNamespace(native={"case": None}),
        Path("unused"),
        None,
        SimpleNamespace(case=None, timeout=1, actions=None),
    )
    result = json.loads(capsys.readouterr().out)
    assert code == (4 if timed_out else 3)
    assert result["status"] == ("timeout" if timed_out else "incomplete")
    assert result["tests"][0]["name"] == name


def test_native_debug_launch_selection_preserves_exact_contract():
    from inspect_cj.cli import NativeDebugUnavailable, native_debug_launch
    from inspect_cj.project import TestResult as Result

    low = {
        "args": ["--filter=Bounds.low"],
        "executable": "/unused/low",
        "cwd": "/private/snapshot",
        "environment": {"FIXTURE": "retained"},
    }
    high = {"args": ["--filter=Bounds.high"], "executable": "/unused/high"}
    cases = [
        Result("low", True, "", "", 0, False, launch=low),
        Result("high", False, "assertion", "", 1, False, launch=high),
    ]
    case, executable, launch = native_debug_launch(cases, "low")
    assert case.name == "low" and case.args == ["--filter=Bounds.low"]
    assert executable == Path("/unused/low") and launch is low
    assert native_debug_launch(cases, None)[0].name == "high"
    with pytest.raises(NativeDebugUnavailable, match="incomplete"):
        native_debug_launch([], None)
    with pytest.raises(ValueError, match="not discovered"):
        native_debug_launch(cases, "unknown")


@pytest.mark.native
@pytest.mark.skipif(os.environ.get("INSPECT_CJ_NATIVE") != "1", reason="requires native compiler")
@pytest.mark.parametrize(
    "compile_only,source,expected_status,expected_exit",
    [
        (True, "main() { 0 }\n", "compile_only", 0),
        (True, 'main() { let value: Int64 = "bad"; 0 }\n', "failed", 2),
        (False, "main() { println(42); 0 }\n", "success", 0),
        (False, "main() { println(43); 0 }\n", "failed", 1),
    ],
)
def test_public_test_preserves_compilation_and_semantic_outcomes(
    tmp_path, compile_only, source, expected_status, expected_exit
):
    from test_standalone import invoke

    implementation = tmp_path / "main.cj"
    implementation.write_text(source)
    manifest = tmp_path / "project.json"
    config = {
        "sources": ["main.cj"],
        "editable": "main.cj",
        "requirement": "Compile only" if compile_only else "Print 42",
        "compile_only": compile_only,
        "tests": [] if compile_only else [{"name": "legacy", "stdout": "42\n"}],
    }
    manifest.write_text(json.dumps(config))
    result = invoke(tmp_path, "test", manifest)
    assert result.returncode == expected_exit, result.output
    report = json.loads(result.output)
    assert report["status"] == expected_status, report
    if expected_status == "compile_only":
        assert report["tests"] == []
    elif not compile_only:
        assert len(report["tests"]) == 1
        assert report["tests"][0]["passed"] == (expected_exit == 0)
    assert implementation.read_text() == source
    assert json.loads(manifest.read_text()) == config


@pytest.mark.parametrize("selected", [None, "missing"])
def test_empty_case_debug_returns_needs_input_before_any_execution(tmp_path, selected):
    source = tmp_path / "main.cj"
    source.write_text("main() { 0 }\n")
    manifest = tmp_path / "project.json"
    config = {
        "sources": ["main.cj"],
        "editable": "main.cj",
        "requirement": "Compile only",
        "compile_only": True,
        "tests": [],
    }
    manifest.write_text(json.dumps(config))
    program = r"""
import contextlib, io, json, sys
from inspect_cj.cli import main
processes = []
def audit(event, args):
    if event == "subprocess.Popen":
        processes.append(str(args[0]))
        raise PermissionError("empty debug contract started execution")
sys.addaudithook(audit)
stream = io.StringIO()
with contextlib.redirect_stdout(stream):
    code = main(sys.argv[1:])
print(json.dumps({"code":code,"result":json.loads(stream.getvalue()),"processes":processes}))
"""
    args = ["debug", str(manifest)]
    if selected is not None:
        args.extend(["--case", selected])
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = run_command([sys.executable, "-c", program, *args], cwd=tmp_path, timeout=5, env=env)
    assert result.returncode == 0, result.output
    observed = json.loads(result.output)
    assert observed["processes"] == [], observed
    assert observed["code"] == 5, observed
    assert observed["result"]["status"] == "needs_input", observed
    assert observed["result"]["missing"] == ["case"], observed
    assert source.read_text() == "main() { 0 }\n"
    assert json.loads(manifest.read_text()) == config


def test_installed_cli_reports_distribution_version(tmp_path):
    from test_standalone import invoke

    result = invoke(tmp_path, "--version")
    assert result.returncode == 0
    assert "0.2.0" in result.output
