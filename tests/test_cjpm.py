"""Actual cjpm/native assertion integration, never flattened cjc arithmetic."""

import json
import os
from pathlib import Path

import pytest
from test_standalone import invoke

from inspect_cj.models import ScriptedModel
from inspect_cj.project import Toolchain
from inspect_cj.repair import RepairController

NATIVE = os.environ.get("INSPECT_CJ_NATIVE") == "1"


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "package"
    (root / "src").mkdir(parents=True)
    (root / "cjpm.toml").write_text("""[package]
name = "bounds"
version = "1.0.0"
cjc-version = "1.1.0"
output-type = "static"
[dependencies]
""")
    (root / "src/lib.cj").write_text("""package bounds

public func boundedIndex(index: Int64, size: Int64): Int64 {
    if (index < 0) { return 0 }
    if (index >= size) { return size }
    return index
}
""")
    (root / "src/lib_test.cj").write_text("""package bounds
import std.unittest.*
import std.unittest.testmacro.*

@Test
public class Bounds {
    @TestCase
    func high() { @Expect(boundedIndex(5, 3), 2) }
    @TestCase
    func low() { @Expect(boundedIndex(-1, 3), 0) }
}
""")
    return root


@pytest.mark.native
@pytest.mark.skipif(not NATIVE, reason="requires installed cjpm/SDK")
def test_native_cjpm_test_exposes_assertion_failures_and_keeps_originals_immutable(
    package, tmp_path
):
    before = {
        str(p.relative_to(package)): p.read_bytes() for p in package.rglob("*") if p.is_file()
    }
    result = invoke(tmp_path, "test", package)
    assert result.returncode == 1, result.output
    report = json.loads(result.output)
    assert len(report["tests"]) == 2
    assert sorted(t["passed"] for t in report["tests"]) == [False, True]
    failure = next(t for t in report["tests"] if not t["passed"])
    assert "left: 3" in failure["actual_stdout"]
    assert failure["launch"]["args"][0] == "--filter=Bounds.high"
    assert failure["name"].startswith("cjpm:")
    after = {str(p.relative_to(package)): p.read_bytes() for p in package.rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.native
@pytest.mark.skipif(not NATIVE, reason="requires installed cjpm/SDK")
def test_native_cjpm_repair_inspects_exact_case_and_accepts_full_suite(package, tmp_path):
    from inspect_cj.native import resolve_package

    original = (package / "src/lib.cj").read_text()
    project = resolve_package(package)
    fixed = original.replace("return size }", "return size - 1 }")
    model = ScriptedModel(
        [
            {
                "role": "inspector",
                "response": {
                    "thought": "Inspect high index before clamp",
                    "actions": [
                        {"action": "set_breakpoint", "line": 5},
                        {"action": "control_execution", "command": "continue"},
                        {"action": "inspect", "variable": "index"},
                        {
                            "action": "propose_repair",
                            "plan": "Clamp to size minus one",
                            "evidence": ["obs-7"],
                        },
                    ],
                },
            },
            {"role": "coder", "response": {"source": fixed, "summary": "Fix upper bound"}},
        ]
    )
    result = RepairController(project, model, tmp_path / "run", tools=Toolchain.discover()).run()
    assert result.status == "success", result
    report = json.loads((tmp_path / "run/report.json").read_text())
    assert sorted(case["passed"] for case in report["iterations"][0]["tests"]) == [False, True]
    assert len(report["iterations"][-1]["tests"]) == 2
    assert all(case["passed"] for case in report["iterations"][-1]["tests"])
    events = [json.loads(line) for line in (tmp_path / "run/events.jsonl").read_text().splitlines()]
    assert any("index = 5" in event.get("observation", {}).get("output", "") for event in events)
    assert (package / "src/lib.cj").read_text() == original
    assert not (package / "target").exists()


@pytest.mark.native
@pytest.mark.skipif(not NATIVE, reason="requires installed cjpm/SDK")
def test_native_cjpm_targeted_false_fix_is_rejected(package, tmp_path):
    from inspect_cj.native import resolve_package

    original = (package / "src/lib.cj").read_text()
    bad = original.replace("return size }", "return size - 1 }").replace("return 0 }", "return 1 }")
    model = ScriptedModel(
        [
            {
                "role": "inspector",
                "response": {
                    "thought": "Inspect index",
                    "actions": [
                        {"action": "set_breakpoint", "line": 5},
                        {"action": "control_execution", "command": "continue"},
                        {"action": "inspect", "variable": "index"},
                        {
                            "action": "propose_repair",
                            "plan": "Fix high clamp",
                            "evidence": ["obs-7"],
                        },
                    ],
                },
            },
            {"role": "coder", "response": {"source": bad, "summary": "Wrong low regression"}},
        ]
    )
    result = RepairController(
        resolve_package(package),
        model,
        tmp_path / "run",
        max_attempts=1,
        tools=Toolchain.discover(),
    ).run()
    assert result.status == "exhausted"
    report = json.loads((tmp_path / "run/report.json").read_text())
    cases = report["iterations"][-1]["tests"]
    assert len(cases) == 2 and sum(case["passed"] for case in cases) == 1
    assert not (tmp_path / "run/repaired.cj").exists()


def test_ambiguous_native_implementation_scope_returns_choices(package, tmp_path):
    (package / "src/other.cj").write_text("package bounds\npublic func other() { 0 }\n")
    result = invoke(tmp_path, "repair", package, "--non-interactive")
    assert result.returncode == 5, result.output
    report = json.loads(result.output)
    assert "editable" in report["missing"]
    assert "src/lib.cj" in report["choices"]


def test_snapshot_preserves_executable_resources(package, tmp_path):
    from inspect_cj.project import Project

    script = package / "resource.sh"
    script.write_text('#!/bin/sh\nprintf "fixture-ok"\n')
    script.chmod(0o750)
    project = Project(
        package,
        ["src/lib.cj"],
        "src/lib.cj",
        "preserve fixture",
        [],
        files=["src/lib.cj", "resource.sh"],
    )
    copied = project.snapshot(tmp_path / "copy")
    assert (copied.root / "resource.sh").stat().st_mode & 0o777 == 0o750


def test_native_xml_missing_case_is_inconclusive(tmp_path):
    from inspect_cj.native import UnsupportedWorkflow, read_xml_cases

    (tmp_path / "suite.xml").write_text(
        '<testsuite tests="2"><testcase name="only" classname="bounds.Bounds"/></testsuite>'
    )
    with pytest.raises(UnsupportedWorkflow, match="count"):
        list(read_xml_cases(tmp_path))


@pytest.mark.native
@pytest.mark.skipif(not NATIVE, reason="requires installed cjpm/SDK")
def test_native_compile_first_preserves_fixture_lifecycle_and_resources(package, tmp_path):
    from inspect_cj.native import resolve_package

    source = package / "src/lib.cj"
    fixed = source.read_text().replace("return size }", "return size - 1 }")
    source.write_text(fixed.replace("return index", 'let bad: Int64 = "bad"; return index'))
    tests = package / "src/lib_test.cj"
    tests.write_text(
        tests.read_text()
        .replace("import std.unittest.*", "import std.fs.*\nimport std.unittest.*")
        .replace(
            "public class Bounds {",
            """public class Bounds {
    var fixture = ""
    @BeforeEach
    func setup() { fixture = String.fromUtf8(File.readFrom("fixtures/value.txt")) }
""",
        )
        .replace("func high() {", 'func high() { @Assert(fixture, "ready");')
    )
    (package / "fixtures").mkdir()
    (package / "fixtures/value.txt").write_text("ready")
    before = tests.read_bytes()
    model = ScriptedModel(
        [{"role": "coder", "response": {"source": fixed, "summary": "Correct the type"}}]
    )
    result = RepairController(resolve_package(package), model, tmp_path / "run").run()
    assert result.status == "success", result
    report = json.loads((tmp_path / "run/report.json").read_text())
    assert report["iterations"][0]["build"]["returncode"] != 0
    assert len(report["iterations"][-1]["tests"]) == 2
    assert all(case["passed"] for case in report["iterations"][-1]["tests"])
    assert tests.read_bytes() == before
    assert (package / "fixtures/value.txt").read_text() == "ready"


@pytest.mark.native
@pytest.mark.skipif(not NATIVE, reason="requires installed cjpm/SDK")
def test_native_multi_package_local_dependency_and_full_scope(package, tmp_path):
    (package / "src/helper").mkdir()
    (package / "src/helper/helper.cj").write_text(
        "package bounds.helper\npublic func one(): Int64 { 1 }\n"
    )
    (package / "src/helper/helper_test.cj").write_text("""package bounds.helper
import std.unittest.*
import std.unittest.testmacro.*
@Test
public class Helper {
    @TestCase
    func value() { @Expect(one(), 1) }
}
""")
    dependency = package / "vendor/localdep"
    (dependency / "src").mkdir(parents=True)
    (dependency / "cjpm.toml").write_text("""[package]
name = "localdep"
version = "1.0.0"
cjc-version = "1.1.0"
output-type = "static"
""")
    (dependency / "src/lib.cj").write_text("package localdep\npublic func offset(): Int64 { 1 }\n")
    manifest = package / "cjpm.toml"
    manifest.write_text(manifest.read_text() + 'localdep = { path = "vendor/localdep" }\n')
    source = package / "src/lib.cj"
    source.write_text(
        source.read_text()
        .replace("package bounds", "package bounds\nimport bounds.helper.*\nimport localdep.*")
        .replace("return size }", "return size - one() - offset() + 1 }")
    )
    result = invoke(tmp_path, "test", package)
    assert result.returncode == 0, result.output
    cases = json.loads(result.output)["tests"]
    assert len(cases) == 3 and all(case["passed"] for case in cases)
    assert {case["launch"]["package"] for case in cases} == {"bounds", "bounds.helper"}
    assert not list(package.rglob("target"))


def test_native_missing_dependency_is_useful_unsupported(package, tmp_path):
    manifest = package / "cjpm.toml"
    manifest.write_text(manifest.read_text() + 'absent = { path = "missing-module" }\n')
    result = invoke(tmp_path, "repair", package, "--non-interactive")
    assert result.returncode == 3, result.output
    report = json.loads(result.output)
    assert "missing-module" in report["reason"]
    assert "missing" in report["reason"]


@pytest.mark.native
@pytest.mark.skipif(not NATIVE, reason="requires installed cjpm/SDK")
def test_native_timeout_cleans_runner_and_package_processes(package, tmp_path):
    from dataclasses import replace

    from inspect_cj.native import resolve_package
    from inspect_cj.process import run_command

    source = package / "src/lib.cj"
    source.write_text("""package bounds
public func boundedIndex(index: Int64, size: Int64): Int64 {
    while (true) {}
    return 0
}
""")
    project = replace(resolve_package(package), test_timeout=2)
    output = tmp_path / "run"
    result = RepairController(project, ScriptedModel([]), output, run_timeout=15).run()
    assert result.status == "exhausted", result
    report = json.loads((output / "report.json").read_text())
    assert report["iterations"][-1]["tests"][0]["timed_out"]
    processes = run_command(["ps", "-axo", "stat=,command="], cwd=tmp_path, timeout=3)
    live = [
        line
        for line in processes.output.splitlines()
        if str(output / "build") in line and not line.lstrip().startswith("Z")
    ]
    assert not live, live


@pytest.mark.native
@pytest.mark.skipif(not NATIVE, reason="requires installed cjpm/SDK")
def test_native_selected_case_with_explicit_file_scope(package, tmp_path):
    (package / "src/other.cj").write_text("package bounds\npublic func other() { 7 }\n")
    initial = invoke(tmp_path, "test", package)
    assert initial.returncode == 1, initial.output
    failure = next(case for case in json.loads(initial.output)["tests"] if not case["passed"])
    source = package / "src/lib.cj"
    fixed = source.read_text().replace("return size }", "return size - 1 }")
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            [
                {
                    "role": "inspector",
                    "response": {
                        "thought": "Inspect selected native index",
                        "actions": [
                            {"action": "set_breakpoint", "line": 5},
                            {"action": "control_execution", "command": "continue"},
                            {"action": "inspect", "variable": "index"},
                            {
                                "action": "propose_repair",
                                "plan": "Fix upper bound",
                                "evidence": ["obs-7"],
                            },
                        ],
                    },
                },
                {"role": "coder", "response": {"source": fixed, "summary": "Fix clamp"}},
            ]
        )
    )
    result = invoke(
        tmp_path,
        "repair",
        package,
        "--case",
        failure["name"],
        "--editable",
        "src/lib.cj",
        "--output",
        tmp_path / "selected",
        "--transport",
        "scripted",
        "--script",
        script,
    )
    assert result.returncode == 0, result.output
    report = json.loads((tmp_path / "selected/report.json").read_text())
    assert report["iterations"][0]["focused_test"] == failure["name"]
    assert {case["name"] for case in report["iterations"][0]["tests"]} == {
        case["name"] for case in report["iterations"][-1]["tests"]
    }
    assert len(report["iterations"][-1]["tests"]) == 2


@pytest.mark.native
@pytest.mark.skipif(not NATIVE, reason="requires installed cjpm/SDK")
@pytest.mark.parametrize("target_form", ["directory", "source", "manifest"])
def test_native_workspace_runs_declared_member_suites(tmp_path, monkeypatch, target_form):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "cjpm.toml").write_text('[workspace]\nmembers = ["first", "second"]\n')
    for name in ("first", "second"):
        module = root / name
        (module / "src").mkdir(parents=True)
        (module / "cjpm.toml").write_text(f'''[package]
name = "{name}"
version = "1.0.0"
cjc-version = "1.1.0"
output-type = "static"
''')
        (module / "src/lib.cj").write_text(
            f"package {name}\npublic func answer(): Int64 {{ 42 }}\n"
        )
        (module / "src/lib_test.cj").write_text(f"""package {name}
import std.unittest.*
import std.unittest.testmacro.*
@Test
public class Values {{
    @TestCase
    func correct() {{ @Expect(answer(), 42) }}
}}
""")
    monkeypatch.setenv("INSPECT_CJ_WORKSPACE_ROOT", str(root))
    target = {
        "directory": root / "first",
        "source": root / "first/src/lib.cj",
        "manifest": root / "first/cjpm.toml",
    }[target_form]
    result = invoke(tmp_path, "test", target)
    assert result.returncode == 0, result.output
    cases = json.loads(result.output)["tests"]
    assert len(cases) == 2 and all(case["passed"] for case in cases)
    assert {case["launch"]["module"] for case in cases} == {"first", "second"}
    assert not list(root.rglob("target"))


@pytest.mark.native
@pytest.mark.skipif(not NATIVE, reason="requires installed cjpm/SDK")
def test_native_cli_cancellation_writes_receipt_and_cleans_descendants(package, tmp_path):
    import signal
    import subprocess
    import sys
    import time

    from inspect_cj.process import run_command

    (package / "src/lib.cj").write_text("""package bounds
public func boundedIndex(index: Int64, size: Int64): Int64 {
    while (true) {}
    return 0
}
""")
    script = tmp_path / "script.json"
    script.write_text("[]")
    output = tmp_path / "cancelled"
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "inspect_cj",
            "repair",
            str(package),
            "--transport",
            "scripted",
            "--script",
            str(script),
            "--output",
            str(output),
            "--run-timeout",
            "20",
        ],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    binary = str(output / "build/0/target/debug/unittest_bin/bounds")
    try:
        end = time.monotonic() + 10
        running = False
        while time.monotonic() < end:
            processes = run_command(
                ["ps", "-axo", "stat=,command="], cwd=tmp_path, timeout=2, output_limit=262144
            )
            if (output / "build/0/discovery.log").exists() and binary in processes.output:
                running = True
                break
            if child.poll() is not None:
                break
            time.sleep(0.05)
        assert running, "native package never reached the real running test"
        os.kill(child.pid, signal.SIGINT)
        stdout, _ = child.communicate(timeout=10)
        assert child.returncode == 130, stdout.decode()
        assert json.loads(stdout)["status"] == "interrupted"
        assert json.loads((output / "report.json").read_text())["status"] == "interrupted"
        processes = run_command(
            ["ps", "-axo", "stat=,command="], cwd=tmp_path, timeout=2, output_limit=262144
        )
        live = [
            line
            for line in processes.output.splitlines()
            if binary in line and not line.lstrip().startswith("Z")
        ]
        assert not live, live
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


@pytest.mark.native
@pytest.mark.skipif(not NATIVE, reason="requires installed cjpm/SDK")
@pytest.mark.parametrize("outcome", ["false_fix", "correct", "cancelled", "restart"])
@pytest.mark.parametrize("initially_present", [True, False])
def test_native_interrupted_fixture_preserves_acceptance_baseline(
    package, tmp_path, outcome, initially_present
):
    from inspect_cj.native import resolve_package

    resource = package / "fixtures/value.txt"
    resource.parent.mkdir()
    if initially_present:
        resource.write_text("2")
    tests = package / "src/lib_test.cj"
    tests.write_text("""package bounds
import std.convert.*
import std.fs.*
import std.unittest.*
import std.unittest.testmacro.*

@Test
public class Bounds {
    var expected = ""
    @BeforeEach
    func setup() {
        expected = String.fromUtf8(File.readFrom("fixtures/value.txt"))
        if (!exists("fixtures")) { Directory.create("fixtures") }
        File.writeTo("fixtures/value.txt", "3".toArray())
    }
    @AfterEach
    func teardown() { File.writeTo("fixtures/value.txt", expected.toArray()) }
    @TestCase
    func high() { @Expect(boundedIndex(5, 3), Int64.parse(expected)) }
    @TestCase
    func low() { @Expect(boundedIndex(-1, 3), 0) }
}
""")
    if not initially_present:
        tests.write_text(
            tests.read_text()
            .replace(
                'expected = String.fromUtf8(File.readFrom("fixtures/value.txt"))',
                'expected = if (exists("fixtures/value.txt")) { '
                'String.fromUtf8(File.readFrom("fixtures/value.txt")) } else { "2" }',
            )
            .replace(
                'File.writeTo("fixtures/value.txt", expected.toArray())',
                'remove("fixtures/value.txt")',
            )
        )
    original = (package / "src/lib.cj").read_text()
    baseline = "2" if initially_present else None
    correct = outcome in ("correct", "restart")
    candidate = original.replace("return size }", "return size - 1 }") if correct else original
    output = tmp_path / "run"
    observed = {}

    class FixtureModel(ScriptedModel):
        def complete(self, role, context, *, timeout=120):
            if role == "inspector" and context["observations"]:
                path = output / "workspace/fixtures/value.txt"
                value = path.read_text() if path.exists() else None
                observed.setdefault("paused", value)
                if outcome == "cancelled":
                    raise KeyboardInterrupt
                if outcome == "restart":
                    if context["state"]["state"] == "Start":
                        observed["restarted"] = value
                    elif "restarted" not in observed:
                        return {
                            "thought": "Restart while the fixture is live",
                            "actions": [{"action": "control_execution", "command": "restart"}],
                        }
            if role == "coder":
                path = output / "workspace/fixtures/value.txt"
                observed["coder"] = path.read_text() if path.exists() else None
            response = super().complete(role, context, timeout=timeout)
            if role == "inspector" and "restarted" in observed:
                response["actions"][0]["evidence"] = ["obs-15"]
                response["actions"] = [
                    {"action": "set_breakpoint", "line": 5},
                    {"action": "control_execution", "command": "continue"},
                    {"action": "inspect", "variable": "index"},
                    *response["actions"],
                ]
            return response

    model = FixtureModel(
        [
            {
                "role": "inspector",
                "response": {
                    "thought": "Stop inside the failing case before fixture teardown",
                    "actions": [
                        {"action": "set_breakpoint", "line": 5},
                        {"action": "control_execution", "command": "continue"},
                        {"action": "inspect", "variable": "index"},
                    ],
                },
            },
            {
                "role": "inspector",
                "response": {
                    "thought": "The live upper bound is wrong",
                    "actions": [
                        {
                            "action": "propose_repair",
                            "plan": "Return size minus one for the upper bound",
                            "evidence": ["obs-7"],
                        }
                    ],
                },
            },
            {"role": "coder", "response": {"source": candidate, "summary": "Try candidate"}},
        ]
    )
    result = RepairController(resolve_package(package), model, output, max_attempts=1).run()
    report = json.loads((output / "report.json").read_text())
    assert observed["paused"] == "3"
    if outcome == "restart":
        assert observed["restarted"] == baseline
    assert sorted(case["passed"] for case in report["iterations"][0]["tests"]) == [False, True]
    restored = output / "workspace/fixtures/value.txt"
    assert (restored.read_text() if restored.exists() else None) == baseline
    if outcome == "cancelled":
        assert result.status == "interrupted", result
        assert len(report["iterations"]) == 1
    else:
        assert result.status == ("success" if correct else "exhausted"), result
        assert observed["coder"] == baseline
        assert len(report["iterations"][-1]["tests"]) == 2
        assert all(case["passed"] for case in report["iterations"][-1]["tests"]) == correct
    assert (resource.read_text() if resource.exists() else None) == baseline
    assert (package / "src/lib.cj").read_text() == original


@pytest.mark.parametrize("command", ["test", "debug", "repair"])
@pytest.mark.parametrize("source_target", [False, True])
def test_editor_boundary_refuses_containing_workspace_before_inventory(
    package, tmp_path, monkeypatch, command, source_target
):
    root = package.parent
    (root / "cjpm.toml").write_text('[workspace]\nmembers = ["package", "sibling"]\n')
    sibling = root / "sibling"
    sibling.mkdir()
    (sibling / "outside.cj").symlink_to(package / "src/lib.cj")
    monkeypatch.setenv("INSPECT_CJ_WORKSPACE_ROOT", str(package))
    target = package / "src/lib.cj" if source_target else package
    result = invoke(tmp_path, command, target)
    report = json.loads(result.output)
    assert result.returncode != 0
    assert "Open and trust the containing workspace" in report["reason"], report
    assert not (package / "target").exists()


@pytest.mark.parametrize("operation", ["test", "replay", "apply"])
def test_nested_manifest_symlink_is_never_opened(package, tmp_path, operation):
    import sys

    from inspect_cj.process import run_command

    external = tmp_path / "outside.toml"
    external.write_text('[package]\nname="outside"\n')
    program = r"""
import contextlib, io, json, os, sys
from pathlib import Path
from inspect_cj.artifacts import finish_candidate, record_invocation
from inspect_cj.cli import main
from inspect_cj.project import Project, Toolchain
root, external, operation = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
run = root.parent / "receipt"
run.mkdir()
project = Project(root, ["src/lib.cj", "src/lib_test.cj"], "src/lib.cj", "Keep tests", [],
                  native={"cjpm":"/bin/echo","case":None},
                  files=["cjpm.toml", "src/lib.cj", "src/lib_test.cj"])
record_invocation(project, Toolchain("/bin/echo", "/bin/echo", None), run)
(run / "repaired.cj").write_text((root / "src/lib.cj").read_text())
finish_candidate(project, run)
(run / "report.json").write_text(json.dumps({"status":"success"}))
(root / "nested").mkdir()
(root / "nested/cjpm.toml").symlink_to(external)
opened = []
def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if path.resolve() == external.resolve(): opened.append(str(path))
sys.addaudithook(audit)
argv = ["test", str(root)] if operation == "test" else (
    ["repair", "--replay", str(run)] if operation == "replay" else ["apply", str(run)])
stream = io.StringIO()
with contextlib.redirect_stdout(stream): code = main(argv)
print(json.dumps({"code":code,"result":json.loads(stream.getvalue()),"opened":opened}))
"""
    env = dict(os.environ, INSPECT_CJ_WORKSPACE_ROOT=str(package))
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = run_command(
        [sys.executable, "-c", program, str(package), str(external), operation],
        cwd=tmp_path,
        env=env,
        timeout=5,
    )
    assert result.returncode == 0, result.output
    observed = json.loads(result.output)
    assert observed["code"] == 3, observed
    assert observed["result"]["status"] == "unsupported"
    assert observed["opened"] == [], observed
    assert external.read_text() == '[package]\nname="outside"\n'


@pytest.mark.native
@pytest.mark.skipif(not NATIVE, reason="requires installed cjpm/SDK")
def test_native_debug_suite_timeout_is_json_and_cleans_descendants(package, tmp_path):
    import signal
    import subprocess
    import sys
    import time

    from inspect_cj.process import run_command

    (package / "src/lib.cj").write_text(
        "package bounds\npublic func boundedIndex(index: Int64, size: Int64): Int64 {\n"
        "    while (true) {}\n    return 0\n}\n"
    )
    actions = tmp_path / "actions.jsonl"
    actions.write_text("")
    scratch = tmp_path / "owned-cli"
    scratch.mkdir()
    env = dict(os.environ, TMPDIR=str(scratch))
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    child = subprocess.Popen(
        [sys.executable, "-m", "inspect_cj", "debug", str(package), "--actions", str(actions)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    observed_pids = set()
    try:
        limit = time.monotonic() + 20
        while time.monotonic() < limit and child.poll() is None:
            listing = run_command(
                ["ps", "-axo", "pid=,ppid=,stat=,comm="],
                cwd=tmp_path,
                timeout=2,
                output_limit=262144,
            )
            rows = [row.split(None, 3) for row in listing.output.splitlines()]
            rows = [row for row in rows if len(row) == 4]
            owned = {child.pid}
            while True:
                descendants = {int(row[0]) for row in rows if int(row[1]) in owned}
                if descendants <= owned:
                    break
                owned.update(descendants)
            observed_pids.update(
                int(row[0])
                for row in rows
                if int(row[0]) in owned
                and Path(row[3]).name == "bounds"
                and not row[2].startswith("Z")
            )
            time.sleep(0.1)
        stdout, stderr = child.communicate(timeout=15)
        assert observed_pids, "the actual native test executable was never observed"
        assert child.returncode == 4, (stdout.decode(), stderr.decode())
        result = json.loads(stdout)
        assert result["status"] == "timeout", result
        assert result["tests"][0]["timed_out"]
        assert "Traceback" not in stderr.decode()
        listing = run_command(
            ["ps", "-axo", "pid=,stat=,comm="], cwd=tmp_path, timeout=2, output_limit=262144
        )
        assert not listing.stdout_truncated
        live = [
            row
            for row in listing.output.splitlines()
            if row.split()
            and row.split()[0].isdigit()
            and int(row.split()[0]) in observed_pids
            and not row.split()[1].startswith("Z")
        ]
        assert not live, live
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


@pytest.mark.parametrize("operation", ["test", "test-source", "replay", "apply"])
@pytest.mark.parametrize(
    "field",
    ["members", "build-members", "test-members", "src-dir", "target-dir", "dependency", "resource"],
)
def test_absolute_manifest_paths_are_rejected_before_execution(package, tmp_path, operation, field):
    from inspect_cj.artifacts import finish_candidate, record_invocation
    from inspect_cj.project import Project

    manifest = package / "cjpm.toml"
    if field in {"members", "build-members", "test-members"}:
        member = package / "member"
        member.mkdir()
        (package / "src").rename(member / "src")
        manifest.rename(member / "cjpm.toml")
        members = [str(member)] if field == "members" else ["member"]
        config = f"[workspace]\nmembers = {json.dumps(members)}\n"
        if field != "members":
            config += f"{field} = {json.dumps([str(member)])}\n"
        manifest.write_text(config)
    elif field in {"src-dir", "target-dir"}:
        manifest.write_text(
            manifest.read_text().replace(
                "[package]", f"[package]\n{field} = {json.dumps(str(package / 'src'))}"
            )
        )
    else:
        section = "dependencies.local" if field == "dependency" else "test-dependencies.local"
        manifest.write_text(
            manifest.read_text() + f"\n[{section}]\npath = {json.dumps(str(package / 'src'))}\n"
        )
    before = {
        str(p.relative_to(package)): p.read_bytes() for p in package.rglob("*") if p.is_file()
    }
    run = tmp_path / "receipt"
    if operation not in {"test", "test-source"}:
        run.mkdir()
        sources = sorted(name for name in before if name.endswith(".cj"))
        editable = next(name for name in sources if not name.endswith("_test.cj"))
        project = Project(
            package,
            sources,
            editable,
            "Preserve assertions",
            [],
            native={"cjpm": "/bin/echo", "case": None},
            files=sorted(before),
        )
        record_invocation(project, Toolchain("/bin/echo", "/bin/echo", None), run)
        (run / "repaired.cj").write_bytes(before[editable])
        finish_candidate(project, run)
        (run / "report.json").write_text(json.dumps({"status": "success"}))
    target = package if operation != "test-source" else next(package.rglob("lib.cj"))
    args = (
        ["test", target]
        if operation in {"test", "test-source"}
        else (["repair", "--replay", run] if operation == "replay" else ["apply", run])
    )
    result = invoke(tmp_path, *args)
    report = json.loads(result.output)
    assert report["status"] == "unsupported", report
    assert "relative" in report["reason"], report
    assert not (tmp_path / "cache").exists()
    assert before == {
        str(p.relative_to(package)): p.read_bytes() for p in package.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize("command", ["test", "debug", "repair"])
@pytest.mark.parametrize("target_form", ["directory", "source", "manifest"])
@pytest.mark.parametrize("location", ["selected", "containing"])
def test_discovery_rejects_manifest_symlinks_before_outside_reads(
    package, tmp_path, command, target_form, location
):
    import shutil
    import sys

    from inspect_cj.process import run_command

    outside = tmp_path / "outside"
    shutil.copytree(package, outside)
    if location == "selected":
        manifest = package / "cjpm.toml"
    else:
        manifest = tmp_path / "cjpm.toml"
        (outside / "cjpm.toml").write_text('[workspace]\nmembers = ["package"]\n')
    manifest.unlink(missing_ok=True)
    manifest.symlink_to(outside / "cjpm.toml")
    target = {
        "directory": package,
        "source": package / "src/lib.cj",
        "manifest": package / "cjpm.toml",
    }[target_form]
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for root in (package, outside)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    program = r"""
import contextlib, io, json, os, sys
from pathlib import Path
from inspect_cj.cli import main
outside, target, command = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
opened, processes = [], []
def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0])).resolve()
        if path.is_relative_to(outside):
            opened.append(str(path))
            raise PermissionError("outside manifest/source access attempted")
    if event == "subprocess.Popen":
        processes.append(str(args[0]))
        raise PermissionError("execution before manifest validation")
sys.addaudithook(audit)
stream = io.StringIO()
with contextlib.redirect_stdout(stream):
    code = main([command, target])
print(json.dumps({"code":code,"result":json.loads(stream.getvalue()),
                  "opened":opened,"processes":processes}))
"""
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    env.pop("INSPECT_CJ_WORKSPACE_ROOT", None)
    result = run_command(
        [sys.executable, "-c", program, str(outside), str(target), command],
        cwd=tmp_path,
        env=env,
        timeout=5,
    )
    assert result.returncode == 0, result.output
    observed = json.loads(result.output)
    assert observed["opened"] == [], observed
    assert observed["processes"] == [], observed
    assert observed["code"] == 3, observed
    assert observed["result"]["status"] == "unsupported", observed
    assert "symlink" in observed["result"]["reason"], observed
    assert not (tmp_path / "cache").exists()
    assert manifest.is_symlink()
    assert before == {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for root in (package, outside)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_compiler_shim_supports_python_paths_with_spaces(tmp_path, monkeypatch):
    import sys

    from inspect_cj.native import environment
    from inspect_cj.process import run_command

    sdk = tmp_path / "sdk"
    (sdk / "bin").mkdir(parents=True)
    compiler = sdk / "bin/cjc"
    compiler.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
    compiler.chmod(0o755)
    interpreter = tmp_path / "python with spaces"
    interpreter.symlink_to(sys.executable)
    monkeypatch.setattr(sys, "executable", str(interpreter))
    tools = Toolchain(str(compiler), "/unused/lldb", None, "/selected sdk")
    env = environment(None, tools, tmp_path / "build")
    result = run_command(["cjc", "--version"], cwd=tmp_path, timeout=5, env=env)
    assert result.returncode == 0
    assert result.output == "--sysroot\n/selected sdk\n--version\n"
