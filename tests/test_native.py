import os
from pathlib import Path

import pytest

from inspect_cj.debugger import CJDBSession
from inspect_cj.project import Project, Toolchain

pytestmark = pytest.mark.native


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    if os.environ.get("INSPECT_CJ_NATIVE") != "1":
        pytest.skip("set INSPECT_CJ_NATIVE=1 to run actual Cangjie/CJDB integration")
    project = Project.load(Path(__file__).resolve().parents[1] / "examples/sum/project.json")
    tools = Toolchain.discover(project.sysroot)
    executable, result = project.build(tmp_path_factory.mktemp("native-build"), tools)
    assert result.returncode == 0, result.output
    return project, tools, executable


def test_native_breakpoint_inspection_mutation_and_restart(native):
    project, tools, executable = native
    with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
        assert session.execute({"action": "inspect"}).status == "invalid"
        assert session.state == "Start"
        assert session.execute({"action": "set_breakpoint", "line": 2}).status == "invalid"
        placed = session.execute({"action": "set_breakpoint", "line": 8})
        assert placed.status == "ok", placed
        assert len(placed.breakpoints) == 1
        paused = session.execute({"action": "control_execution", "command": "continue"})
        assert paused.state == "RuntimeState", paused
        assert paused.frames[0]["line"] == 8
        assert paused.breakpoints == []
        variables = session.execute({"action": "inspect"})
        assert "total = 6" in variables.output
        assert "n = 4" in variables.output
        bad = session.execute({"action": "inspect", "variable": "does_not_exist"})
        assert bad.status == "error"
        assert bad.state == "RuntimeError"
        assert session.execute({"action": "inspect", "variable": "total"}).status == "ok"
        mutation = session.execute({"action": "interact_code", "code": "set total = 10"})
        assert mutation.status in {"ok", "unsupported"}, mutation
        continued = session.execute({"action": "control_execution", "command": "continue"})
        assert continued.state == "Done", continued
        assert ("10" if mutation.status == "ok" else "6") in continued.output
        restarted = session.execute({"action": "control_execution", "command": "restart"})
        assert restarted.state == "Start"
        session.execute({"action": "set_breakpoint", "line": 8})
        session.execute({"action": "control_execution", "command": "continue"})
        assert "total = 6" in session.execute({"action": "inspect"}).output


def test_native_expression_error_is_recoverable_and_commands_are_validated(native):
    project, tools, executable = native
    with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
        session.execute({"action": "set_breakpoint", "line": 8})
        session.execute({"action": "control_execution", "command": "continue"})
        locals_ = session.execute({"action": "inspect"})
        assert "total = 6" in locals_.output and "n = 4" in locals_.output
        expr = session.execute({"action": "interact_code", "code": "expression total + n"})
        assert expr.status == "ok", expr
        assert "= 10" in expr.output, expr
        assert session.execute({"action": "inspect", "variable": "n"}).status == "ok"
        for code in ("script import os", "print n\nquit", "set x = 1; quit"):
            assert session.execute({"action": "interact_code", "code": code}).status == "invalid"


def test_native_failed_exit_has_no_fabricated_frames(native):
    project, tools, executable = native
    from inspect_cj.project import TestCase

    case = TestCase("bad-input", ["invalid"], "", "", 0)
    with CJDBSession(project, executable, case, tools=tools) as session:
        failed = session.execute({"action": "control_execution", "command": "continue"})
        assert failed.state == "PostMortem", failed
        assert session.execute({"action": "set_breakpoint", "line": 8}).status == "invalid"
        assert (
            session.execute({"action": "control_execution", "command": "restart"}).state == "Start"
        )


def test_native_infinite_output_is_bounded_and_inferior_is_killed(tmp_path, native):
    import json
    import signal

    _, tools, _ = native
    (tmp_path / "loop.cj").write_text('main() { while (true) { println("FLOOD") }; 0 }')
    manifest = tmp_path / "project.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": ["loop.cj"],
                "editable": "loop.cj",
                "requirement": "terminate",
                "tests": [{"name": "loop", "stdout": ""}],
            }
        )
    )
    project = Project.load(manifest)
    executable, build = project.build(tmp_path / "build", tools)
    assert build.returncode == 0, build.output
    with CJDBSession(project, executable, project.tests[0], tools=tools, timeout=0.3) as session:
        timed_out = session.execute({"action": "control_execution", "command": "continue"})
        assert timed_out.status == "timeout", timed_out
        assert len(timed_out.output) < 17000
        assert "FLOOD" in timed_out.output
        pid = session.context["pid"]
        with pytest.raises(ProcessLookupError):
            os.kill(pid, signal.SIGCONT)
        assert (
            session.execute({"action": "control_execution", "command": "restart"}).state == "Start"
        )


def test_native_repair_revalidates_entire_suite_and_selects_new_failure(native, tmp_path):
    import copy
    import json

    from inspect_cj.models import ScriptedModel
    from inspect_cj.repair import RepairController

    project, tools, _ = native
    original = (project.root / project.editable).read_text()
    contexts = []

    class RecordingModel(ScriptedModel):
        def complete(self, role, context, *, timeout=120):
            contexts.append((role, copy.deepcopy(context)))
            return super().complete(role, context, timeout=timeout)

    inspect = {
        "role": "inspector",
        "response": {
            "thought": "Observe the accumulator.",
            "actions": [
                {"action": "set_breakpoint", "line": 8},
                {"action": "control_execution", "command": "continue"},
                {"action": "inspect"},
            ],
        },
    }
    propose = {
        "role": "inspector",
        "response": {
            "thought": "Runtime values reveal the boundary.",
            "actions": [
                {
                    "action": "propose_repair",
                    "plan": "The exclusive range omits n. Include it.",
                    "evidence": ["obs-7"],
                }
            ],
        },
    }
    model = RecordingModel(
        [
            inspect,
            propose,
            {
                "role": "coder",
                "response": {
                    "source": original.replace("return total", "return 10"),
                    "summary": "partial attempt",
                },
            },
            inspect,
            propose,
            {
                "role": "coder",
                "response": {"source": original.replace("1..n", "1..=n"), "summary": "include n"},
            },
        ]
    )
    output = tmp_path / "repair"
    result = RepairController(project, model, output, tools=tools).run()
    assert result.status == "success", result
    assert result.patch_attempts == 2
    assert result.inference_steps == 6
    report = json.loads((output / "report.json").read_text())
    assert report["iterations"][0]["focused_test"] == "four"
    assert report["iterations"][1]["focused_test"] == "one"
    assert all(test["passed"] for test in report["iterations"][-1]["tests"])
    assert (project.root / project.editable).read_text() == original
    assert "1..=n" in (output / "repaired.cj").read_text()
    coder_contexts = [context for role, context in contexts if role == "coder"]
    assert "repair_plan" in coder_contexts[0]
    assert "observations" not in coder_contexts[0]
    assert len(coder_contexts[1]["memory"]) == 1
    inspector_contexts = [context for role, context in contexts if role == "inspector"]
    assert inspector_contexts[2]["memory"] == []
    from dataclasses import asdict

    for _, context in contexts:
        assert context.get("tests") == [asdict(case) for case in project.tests]


def test_native_repair_rejects_ungrounded_plan_and_obeys_inference_budget(native, tmp_path):
    from inspect_cj.models import ScriptedModel
    from inspect_cj.repair import RepairController

    project, tools, _ = native
    response = {
        "role": "inspector",
        "response": {
            "thought": "Guess",
            "actions": [
                {"action": "propose_repair", "plan": "Guess a change", "evidence": ["invented"]}
            ],
        },
    }
    result = RepairController(
        project, ScriptedModel([response, response]), tmp_path / "budget", tools=tools, max_steps=2
    ).run()
    assert result.status == "exhausted"
    assert result.inference_steps == 2
    assert result.patch_attempts == 0


def test_native_stdin_fixture_reaches_debugger_without_consuming_actions(native):
    from inspect_cj.project import run_suite

    _, tools, _ = native
    project = Project.load(Path(__file__).resolve().parents[1] / "examples/stdin/project.json")
    import tempfile

    with tempfile.TemporaryDirectory(prefix="inspect-cj-stdin-test-") as directory:
        executable, build = project.build(Path(directory), tools)
        assert build.returncode == 0, build.output
        assert all(result.passed for result in run_suite(project, executable, tools))
        with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
            placed = session.execute({"action": "set_breakpoint", "line": 6})
            assert placed.status == "ok", placed
            stopped = session.execute({"action": "control_execution", "command": "continue"})
            assert stopped.state == "RuntimeState", stopped
            variables = session.execute({"action": "inspect"})
            assert "n = 4" in variables.output
            finished = session.execute({"action": "control_execution", "command": "continue"})
            assert finished.state == "Done"
            assert finished.output == "8\n"


def test_native_malformed_model_json_is_feedback_until_budget_exhausts(native, tmp_path):
    from inspect_cj.models import ScriptedModel
    from inspect_cj.repair import RepairController

    project, tools, _ = native
    model = ScriptedModel([{"role": "inspector", "response": []}])
    result = RepairController(
        project, model, tmp_path / "malformed", tools=tools, max_steps=1
    ).run()
    assert result.status == "exhausted"
    assert result.inference_steps == 1
    assert result.patch_attempts == 0


def test_native_function_wrapper_builds_multiple_sources(native, tmp_path):
    from inspect_cj.project import run_suite

    _, tools, _ = native
    project = Project.load(Path(__file__).resolve().parents[1] / "examples/function/project.json")
    executable, build = project.build(tmp_path / "function-build", tools)
    assert build.returncode == 0, build.output
    assert [result.passed for result in run_suite(project, executable, tools)] == [
        False,
        True,
        True,
    ]


def test_native_patch_budget_prevents_an_extra_candidate(native, tmp_path):
    from inspect_cj.models import ScriptedModel
    from inspect_cj.repair import RepairController

    project, tools, _ = native
    original = (project.root / project.editable).read_text()
    model = ScriptedModel(
        [
            {
                "role": "inspector",
                "response": {
                    "thought": "Observe",
                    "actions": [
                        {"action": "set_breakpoint", "line": 8},
                        {"action": "control_execution", "command": "continue"},
                        {"action": "inspect"},
                    ],
                },
            },
            {
                "role": "inspector",
                "response": {
                    "thought": "Plan",
                    "actions": [
                        {
                            "action": "propose_repair",
                            "plan": "Include the upper bound.",
                            "evidence": ["obs-7"],
                        }
                    ],
                },
            },
            {
                "role": "coder",
                "response": {"source": original, "summary": "Unchanged failing candidate"},
            },
        ]
    )
    result = RepairController(
        project, model, tmp_path / "patch-limit", tools=tools, max_attempts=1
    ).run()
    assert result.status == "exhausted"
    assert result.reason == "patch attempt budget exhausted"
    assert result.patch_attempts == 1
    assert result.inference_steps == 3


def test_native_utf8_output_matches_executable_across_fifo_chunks(native, tmp_path):
    import json

    from inspect_cj.project import run_suite

    _, tools, _ = native
    expected = "漢" * 3000 + "\n"
    (tmp_path / "unicode.cj").write_text('main() { println("' + "漢" * 3000 + '"); 0 }')
    manifest = tmp_path / "project.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": ["unicode.cj"],
                "editable": "unicode.cj",
                "requirement": "Print Unicode",
                "tests": [{"name": "unicode", "stdout": expected}],
            }
        )
    )
    project = Project.load(manifest)
    executable, build = project.build(tmp_path / "build", tools)
    assert build.returncode == 0, build.output
    assert all(result.passed for result in run_suite(project, executable, tools))
    with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
        result = session.execute({"action": "control_execution", "command": "continue"})
        assert result.state == "Done", result
        assert result.output == expected


@pytest.fixture(scope="module")
def descendant_program(native, tmp_path_factory):
    import json

    _, tools, _ = native
    root = tmp_path_factory.mktemp("native-descendants")
    (root / "child.cj").write_text("""import std.process.*
main(args: Array<String>): Int64 {
    let child = launch("/bin/sh", ["-c", "echo $$ > " + args[0] + "; exec /bin/sleep 30"])
    let waitForChild = args[1] != "exit"
    if (waitForChild) {
        child.wait()
    }
    return 0
}
""")
    manifest = root / "project.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": ["child.cj"],
                "editable": "child.cj",
                "requirement": "Own the child lifetime",
                "tests": [{"name": "child", "stdout": ""}],
            }
        )
    )
    project = Project.load(manifest)
    executable, build = project.build(root / "build", tools)
    assert build.returncode == 0, build.output
    return project, tools, executable


@pytest.mark.parametrize(
    "ending", ["timeout", "restart", "close", "exited", "bridge-failure", "interrupt"]
)
def test_native_lifecycle_reaps_ordinary_inferior_descendants(descendant_program, tmp_path, ending):
    import signal
    import subprocess
    import time

    from inspect_cj.project import TestCase

    project, tools, executable = descendant_program
    pidfile = tmp_path / "child.pid"
    case = TestCase("child", [str(pidfile), "exit" if ending == "exited" else "wait"], "", "", 0)
    child_pid = None
    group = None
    session = CJDBSession(project, executable, case, tools=tools, timeout=3)
    try:
        if ending == "interrupt":

            def interrupt(*_args):
                raise KeyboardInterrupt

            old_handler = signal.signal(signal.SIGALRM, interrupt)
            try:
                signal.setitimer(signal.ITIMER_REAL, 0.8)
                with pytest.raises(KeyboardInterrupt):
                    session.execute({"action": "control_execution", "command": "continue"})
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, old_handler)
        else:
            assert session.execute({"action": "set_breakpoint", "line": 5}).status == "ok"
            paused = session.execute({"action": "control_execution", "command": "continue"})
            assert paused.state == "RuntimeState", paused
            group = session.context["pid"]

        deadline = time.monotonic() + 2
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        child_pid = int(pidfile.read_text())
        if ending == "timeout":
            session.timeout = 0.2
            result = session.execute({"action": "control_execution", "command": "continue"})
            assert result.status == "timeout", result
        elif ending == "restart":
            result = session.execute({"action": "control_execution", "command": "restart"})
            assert result.state == "Start", result
        elif ending == "exited":
            result = session.execute({"action": "control_execution", "command": "continue"})
            assert result.state == "Done", result
        elif ending == "bridge-failure":
            os.kill(session._process.pid, signal.SIGKILL)
            failed = session.execute({"action": "inspect"})
            assert failed.status == "error"
            assert failed.state == "PostMortem"
            assert not failed.live
            assert failed.frames == failed.breakpoints == []
            snapshot = session.snapshot()
            assert snapshot["state"] == "PostMortem"
            assert snapshot["frames"] == snapshot["breakpoints"] == []
            assert session.execute({"action": "inspect"}).state == "PostMortem"
        session.close()
        deadline = time.monotonic() + 2
        while True:
            status = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(child_pid)],
                capture_output=True,
                text=True,
                check=False,
            )
            alive = status.returncode == 0 and not status.stdout.strip().startswith("Z")
            if not alive or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        assert not alive, f"ordinary native descendant {child_pid} survived {ending}"
    finally:
        session.close()
        # Independent test cleanup also prevents a regressed implementation leaking a child.
        if pidfile.exists() and child_pid is None:
            child_pid = int(pidfile.read_text())
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if group is not None:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_native_symlinked_source_build_and_breakpoints(native, tmp_path):
    import json

    project, tools, _ = native
    real = tmp_path / "real"
    real.mkdir()
    (real / "main.cj").write_text((project.root / project.editable).read_text())
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)
    config = json.loads((project.root / "project.json").read_text())
    config.update(sources=["alias/main.cj"], editable="alias/main.cj")
    manifest = tmp_path / "project.json"
    manifest.write_text(json.dumps(config))
    aliased = Project.load(manifest)
    executable, build = aliased.build(tmp_path / "build", tools)
    assert build.returncode == 0, build.output
    with CJDBSession(aliased, executable, aliased.tests[0], tools=tools) as session:
        placed = session.execute({"action": "set_breakpoint", "line": 8})
        assert placed.status == "ok", placed
        stopped = session.execute({"action": "control_execution", "command": "continue"})
        assert stopped.state == "RuntimeState", stopped
        assert stopped.frames[0]["file"] == str(real / "main.cj")
        assert "total = 6" in session.execute({"action": "inspect"}).output
        assert (
            session.execute({"action": "control_execution", "command": "restart"}).state == "Start"
        )
        placed = session._request("breakpoint", file=str(tmp_path / "alias/main.cj"), line=8)
        assert placed["ok"], placed
        stopped = session.execute({"action": "control_execution", "command": "continue"})
        assert stopped.state == "RuntimeState", stopped
        assert (
            session.execute({"action": "control_execution", "command": "continue"}).state == "Done"
        )


def test_native_repair_rejects_latest_alias_after_live_inspection(native, tmp_path):
    from inspect_cj.models import ScriptedModel
    from inspect_cj.repair import RepairController

    project, tools, _ = native
    model = ScriptedModel(
        [
            {
                "role": "inspector",
                "response": {
                    "thought": "Observe",
                    "actions": [
                        {"action": "set_breakpoint", "line": 8},
                        {"action": "control_execution", "command": "continue"},
                        {"action": "inspect"},
                    ],
                },
            },
            {
                "role": "inspector",
                "response": {
                    "thought": "Plan",
                    "actions": [
                        {"action": "propose_repair", "plan": "Include n", "evidence": ["latest"]},
                    ],
                },
            },
        ]
    )
    result = RepairController(
        project, model, tmp_path / "alias-rejected", tools=tools, max_steps=2
    ).run()
    assert result.status == "exhausted"
    assert result.patch_attempts == 0
    assert result.inference_steps == 2


def test_native_debugger_requests_consume_absolute_deadline(native):
    import time

    project, tools, executable = native
    with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
        session.execute({"action": "set_breakpoint", "line": 8})
        session.execute({"action": "control_execution", "command": "continue"})
        session.deadline = time.monotonic() - 1
        observation = session.execute({"action": "inspect", "variable": "total"})
        assert observation.status == "error"
        assert "deadline" in observation.output.lower()


def test_native_compile_repair_respects_attempt_allowance(native, tmp_path):
    from inspect_cj.models import ScriptedModel
    from inspect_cj.project import Project
    from inspect_cj.project import TestCase as Case
    from inspect_cj.repair import RepairController

    _, tools, _ = native
    broken = 'main() { let n: Int64 = "invalid"; println(n); 0 }\n'
    (tmp_path / "main.cj").write_text(broken)
    project = Project(
        tmp_path, ["main.cj"], "main.cj", "print 42", [Case("answer", [], "", "42\n")]
    )
    model = ScriptedModel([{"role": "coder", "response": {"source": broken, "summary": "bad"}}])
    result = RepairController(project, model, tmp_path / "run", tools=tools, max_attempts=1).run()
    assert result.status == "exhausted"
    assert result.patch_attempts == 1
    assert not (tmp_path / "run/repaired.cj").exists()
