"""Real evaluator regressions, including supported side effects and SDK limits."""

import json
import os
import re
import signal

import pytest

from inspect_cj.debugger import CJDBSession
from inspect_cj.project import Project, Toolchain
from inspect_cj.project import TestCase as Case

pytestmark = pytest.mark.native

PROGRAM = """struct Box {
    let value: Int64
    init(value: Int64) { this.value = value }
}
var counter: Int64 = 0
func bump(): Int64 {
    counter += 1
    return counter
}
func typed(n: Int64): Int64 {
    let flag = true
    let fraction = 2.5
    let items = [3, 5, 7]
    let box = Box(9)
    var total = n
    if (flag && fraction > 0.0) {
        total += 1 // typed-stop
    }
    return total + items[1] + box.value
}
func scoped(n: Int64): Int64 {
    var total = 0
    for (i in 0..n) {
        total += i
        if (i == 2) {
            let lexical = total + 100
            total += lexical // lexical-stop
        }
    }
    return total
}
func wrapper(n: Int64): Int64 {
    let answer = scoped(n)
    return answer
}
func recursive(n: Int64): Int64 {
    if (n == 0) { return 1 }
    let subtotal = recursive(n - 1)
    return subtotal + n // recursive-stop
}
main(): Int64 {
    println(typed(4))
    println(wrapper(4))
    println(recursive(3))
    println(counter)
    println(bump())
    return 0
}
"""


@pytest.fixture(scope="module")
def evaluator(tmp_path_factory):
    if os.environ.get("INSPECT_CJ_NATIVE") != "1":
        pytest.skip("set INSPECT_CJ_NATIVE=1 to run actual Cangjie/CJDB integration")
    root = tmp_path_factory.mktemp("native-expressions")
    source = root / "program.cj"
    source.write_text(PROGRAM)
    project = Project(
        root,
        [source.name],
        source.name,
        "Exercise native expressions",
        [Case("expressions", [], "", "19\n109\n7\n0\n1\n")],
    )
    tools = Toolchain.discover()
    executable, build = project.build(root / "build", tools)
    assert build.returncode == 0, build.output
    yield project, tools, executable
    assert source.read_text() == PROGRAM


def stop(session, marker):
    line = next(i for i, text in enumerate(PROGRAM.splitlines(), 1) if marker in text)
    placed = session.execute({"action": "set_breakpoint", "line": line})
    assert placed.status == "ok", placed
    paused = session.execute({"action": "control_execution", "command": "continue"})
    assert paused.state == "RuntimeState", paused
    assert paused.frames[0]["line"] == line
    return paused


def expression(session, code, expected):
    result = session.execute({"action": "interact_code", "code": "expression " + code})
    print(json.dumps({"code": code, **result.to_dict()}))
    assert result.status == "ok", result
    assert re.search(r" = " + re.escape(expected) + r"\s*$", result.output), result
    return result


def test_native_typed_expressions_repetition_and_once_only_side_effect(evaluator):
    project, tools, executable = evaluator
    with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
        stop(session, "typed-stop")
        for code, expected in [
            ("3", "3"),
            ("1 + 2", "3"),
            ("n + total", "8"),
            ("n > 2", "true"),
            ("flag && true", "true"),
            ("fraction + 1.25", "3.75"),
            ("box.value", "9"),
            ("n + total", "8"),
            ("bump()", "1"),
            ("counter", "1"),
            ("total = total + 1", "()"),
            ("total", "5"),
        ]:
            expression(session, code, expected)
        mutation = session.execute({"action": "interact_code", "code": "set total = 8"})
        assert mutation.status == "ok", mutation
        expression(session, "total + n", "12")
        assert session.capabilities["expression"] == "supported"
        assert session.capabilities["mutation"] == "supported"
        finished = session.execute({"action": "control_execution", "command": "continue"})
        assert finished.state == "Done", finished
        assert finished.output == "23\n109\n7\n1\n2\n"
        pid = session.context["pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, signal.SIGCONT)


def test_native_loop_lexical_scope_and_selected_caller_frame(evaluator):
    project, tools, executable = evaluator
    with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
        stop(session, "lexical-stop")
        visible = session.execute({"action": "inspect"})
        assert "lexical = 103" in visible.output
        assert "i = 2" in visible.output
        expression(session, "i + total", "5")
        expression(session, "lexical + total", "106")
        caller = session.execute({"action": "inspect", "frame": 1})
        assert caller.status == "ok", caller
        assert "n = 4" in caller.output
        expression(session, "n + 20", "24")
        assert session.execute({"action": "inspect", "frame": 0}).status == "ok"
        expression(session, "lexical + n", "107")


def test_native_recursion_uses_each_selected_frame(evaluator):
    project, tools, executable = evaluator
    with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
        stop(session, "recursive-stop")
        expression(session, "subtotal + n", "2")
        assert session.execute({"action": "inspect", "frame": 1}).status == "ok"
        expression(session, "n + 10", "12")
        assert session.execute({"action": "inspect", "frame": 2}).status == "ok"
        expression(session, "n + 10", "13")
        assert session.execute({"action": "inspect", "frame": 0}).status == "ok"
        expression(session, "n + 10", "11")


def test_native_restart_and_new_sessions_reset_runtime_and_capabilities(evaluator):
    project, tools, executable = evaluator
    pids = []
    for _ in range(2):
        with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
            for launch in range(2):
                stop(session, "typed-stop")
                pids.append(session.context["pid"])
                expression(session, "counter", "0")
                expression(session, "bump()", "1")
                expression(session, "total + n", "8")
                if launch == 0:
                    restarted = session.execute(
                        {"action": "control_execution", "command": "restart"}
                    )
                    assert restarted.state == "Start"
                    assert session.capabilities == {
                        "inspection": True,
                        "mutation": "unknown",
                        "expression": "unknown",
                    }
    assert len(set(pids)) == 4
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, signal.SIGCONT)


def test_native_index_failure_remains_inspectable_and_is_not_blanket_support(evaluator):
    project, tools, executable = evaluator
    with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
        stop(session, "typed-stop")
        indexed = session.execute({"action": "interact_code", "code": "expression items[1]"})
        print(json.dumps({"code": "items[1]", **indexed.to_dict()}))
        # A native execution fault is not evidence that the indexing grammar is unsupported.
        if indexed.status == "ok":
            assert "= 5" in indexed.output
        else:
            assert indexed.status == "error", indexed
            assert indexed.state == "RuntimeError", indexed
            assert session.capabilities["expression"] != "unsupported"
            assert re.search(r"signal SIG(?:SEGV|BUS)", indexed.output), indexed
        assert session.execute({"action": "inspect", "variable": "items[1]"}).status == "ok"
        expression(session, "n + total", "8")
        assert session.capabilities["expression"] == "supported"
        finished = session.execute({"action": "control_execution", "command": "continue"})
        assert finished.state == "Done", finished
        assert finished.output == project.tests[0].stdout


def test_native_existing_mappings_are_preserved_and_missing_metadata_recovers(evaluator):
    project, tools, executable = evaluator
    with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
        stop(session, "typed-stop")
        expression(session, "n + total", "8")
        before = session._request("interact", code="image list -o -f")["output"]
        expression(session, "n + total", "8")
        assert session._request("interact", code="image list -o -f")["output"] == before
        clear = session._request(
            "interact",
            code="script t=lldb.debugger.GetSelectedTarget(); "
            "m=next(m for m in t.modules if m.file.basename=='libcangjie-runtime.dylib'); "
            "print(t.ClearModuleLoadAddress(m))",
        )
        assert clear["ok"], clear
        expression(session, "n + total", "8")
        assert session._request("interact", code="image list -o -f")["output"] == before


def test_native_conflicting_mapping_fails_without_executing_or_overwriting(evaluator):
    project, tools, executable = evaluator
    with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
        stop(session, "typed-stop")
        expression(session, "n + total", "8")
        conflict = session._request(
            "interact",
            code="script t=lldb.debugger.GetSelectedTarget(); "
            "m=next(m for m in t.modules if m.file.basename=='libcangjie-runtime.dylib'); "
            "s=m.FindSection('__DATA'); "
            "bad=s.GetLoadAddress(t)+4096; print(t.SetSectionLoadAddress(s,bad))",
        )
        assert conflict["ok"], conflict
        before = session._request("interact", code="image dump sections libcangjie-runtime.dylib")
        for _ in range(2):
            result = session.execute({"action": "interact_code", "code": "expression bump()"})
            assert result.status == "error", result
            assert "conflict" in result.output and "not executed" in result.output
        after = session._request("interact", code="image dump sections libcangjie-runtime.dylib")
        assert after["output"] == before["output"]
        assert "counter = 0" in session.execute({"action": "inspect", "variable": "counter"}).output


@pytest.mark.parametrize("delayed_symbol", ["CJ_MCC_HandleSafepoint", "CJ_MCC_GetExceptionWrapper"])
def test_native_slow_symbol_read_never_executes_expression_and_restart_recovers(
    evaluator, delayed_symbol
):
    import time

    project, tools, executable = evaluator
    with CJDBSession(project, executable, project.tests[0], tools=tools) as session:
        stop(session, "typed-stop")
        expression(session, "counter", "0")
        # Fault injection wraps the real SB read in this disposable debugger only.
        patch = """import gc,cjdb_runtime,time
sync=next(o for o in gc.get_objects() if isinstance(o,cjdb_runtime.RuntimeModules))
runtime=next(m for m in sync.target.modules if m.file.basename=='libcangjie-runtime.dylib')
delayed=runtime.FindSymbol(SYMBOL,lldb.eSymbolTypeCode).GetStartAddress().GetLoadAddress(sync.target)
class SlowRead:
    def __init__(self,original):
        self.original=original
        self.slept=False
    def ReadMemory(self,load,size,error):
        value=self.original.ReadMemory(load,size,error)
        if load==delayed and size==1 and not self.slept:
            self.slept=True
            time.sleep(4.25)
        return value
sync.process=SlowRead(sync.process)
""".replace("SYMBOL", repr(delayed_symbol))
        setup = session._request("interact", code="script exec(" + repr(patch) + ")")
        assert setup["ok"], setup
        start = time.monotonic()
        failed = session.execute({"action": "interact_code", "code": "expression bump()"})
        seconds = time.monotonic() - start
        counter = session.execute({"action": "inspect", "variable": "counter"})
        print(
            json.dumps(
                {
                    "delayed_symbol": delayed_symbol,
                    "seconds": seconds,
                    "result": failed.to_dict(),
                    "counter": counter.to_dict(),
                }
            )
        )
        assert failed.status == "error", failed
        assert "time budget" in failed.output and "not executed" in failed.output
        assert "counter = 0" in counter.output
        cached = session.execute({"action": "interact_code", "code": "expression bump()"})
        assert cached.status == "error" and cached.output == failed.output
        assert "counter = 0" in session.execute({"action": "inspect", "variable": "counter"}).output
        assert (
            session.execute({"action": "control_execution", "command": "restart"}).state == "Start"
        )
        stop(session, "typed-stop")
        expression(session, "counter", "0")
        expression(session, "bump()", "1")
        expression(session, "counter", "1")
