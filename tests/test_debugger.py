import pytest

from inspect_cj.debugger import CJDBSession


@pytest.mark.parametrize(
    "diagnostic,expected",
    [
        (
            "error: unsupported expression\n"
            "error: Execution was interrupted, reason: signal SIGBUS.",
            "error",
        ),
        (
            "error: unsupported expression\nerror: expression failed to parse:\nunknown error",
            "error",
        ),
        ("error: operation not supported by this target", "unsupported"),
    ],
)
def test_expression_status_distinguishes_generic_wrapper_from_explicit_unavailability(
    diagnostic, expected
):
    session = CJDBSession.__new__(CJDBSession)
    session._closed = False
    session._sequence = 0
    session.state = "RuntimeState"
    session.context = {"mode": "stopped"}
    session.capabilities = {"inspection": True, "expression": "unknown", "mutation": "unknown"}
    session._request = lambda *args, **kwargs: {"ok": False, "output": diagnostic}
    result = session.execute({"action": "interact_code", "code": "expression items[1]"})
    assert result.status == expected
    if expected == "error":
        assert result.state == "RuntimeError"
        assert session.capabilities["expression"] == "unknown"
    else:
        assert session.capabilities["expression"] == "unsupported"
