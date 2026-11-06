"""Hash/path guards protect user-owned sources from stale application."""

import json

import pytest

from inspect_cj import artifacts
from inspect_cj.project import Project, Toolchain
from inspect_cj.project import TestCase as Case


def prepared(tmp_path):
    source = tmp_path / "main.cj"
    source.write_text("main() { 1 }\n")
    run = tmp_path / "run"
    run.mkdir()
    tools = Toolchain("/bin/echo", "/bin/echo", None)
    project = Project(tmp_path, ["main.cj"], "main.cj", "return zero", [Case("zero", [], "", "")])
    artifacts.record_invocation(project, tools, run)
    candidate = run / "repaired.cj"
    candidate.write_text("main() { 0 }\n")
    artifacts.finish_candidate(project, run)
    (run / "report.json").write_text(json.dumps({"status": "success"}))
    return source, run


def test_apply_is_explicit_and_refuses_stale_source(tmp_path):
    source, run = prepared(tmp_path)
    assert source.read_text() == "main() { 1 }\n"
    source.write_text("main() { 2 }\n")
    with pytest.raises(ValueError, match="changed"):
        artifacts.apply_candidate(run)
    assert source.read_text() == "main() { 2 }\n"


def test_apply_checks_candidate_identity_and_path_and_preserves_mode(tmp_path):
    source, run = prepared(tmp_path)
    source.chmod(0o640)
    artifacts.apply_candidate(run)
    assert source.read_text() == "main() { 0 }\n"
    assert source.stat().st_mode & 0o777 == 0o640
    assert "-main() { 1 }" in (run / "candidate.diff").read_text()
    with pytest.raises(ValueError, match="changed"):
        artifacts.apply_candidate(run)


def test_apply_rejects_tampered_candidate_and_source_symlink(tmp_path):
    source, run = prepared(tmp_path)
    (run / "repaired.cj").write_text("malicious")
    with pytest.raises(ValueError, match="candidate.*changed"):
        artifacts.apply_candidate(run)
    (run / "repaired.cj").write_text("main() { 0 }\n")
    other = tmp_path / "other.cj"
    source.rename(other)
    source.symlink_to(other)
    with pytest.raises(ValueError, match="path|symlink"):
        artifacts.apply_candidate(run)
    assert other.read_text() == "main() { 1 }\n"


def test_apply_refuses_changed_independent_driver(tmp_path):
    source, run = prepared(tmp_path)
    driver = tmp_path / "driver.cj"
    driver.write_text("main() { println(1); 0 }")
    record = json.loads((run / "invocation.json").read_text())
    record["sources"]["driver.cj"] = artifacts.identity(driver)
    (run / "invocation.json").write_text(json.dumps(record))
    driver.write_text("main() { println(2); 0 }")
    with pytest.raises(ValueError, match="acceptance.*changed"):
        artifacts.apply_candidate(run)
    assert source.read_text() == "main() { 1 }\n"


@pytest.mark.parametrize("operation", ["apply", "replay"])
def test_native_receipt_refuses_added_acceptance_files(tmp_path, operation):
    from test_standalone import invoke

    root = tmp_path / "package"
    root.mkdir()
    (root / "lib.cj").write_text("package example\npublic func value() { 1 }\n")
    (root / "cjpm.toml").write_text('[package]\nname = "example"\n')
    run = tmp_path / "run"
    run.mkdir()
    tools = Toolchain("/bin/echo", "/bin/echo", None)
    project = Project(
        root,
        ["lib.cj"],
        "lib.cj",
        "preserve acceptance",
        [],
        native={"cjpm": "/bin/echo", "case": None},
        files=["cjpm.toml", "lib.cj"],
    )
    artifacts.record_invocation(project, tools, run)
    (run / "repaired.cj").write_text("package example\npublic func value() { 2 }\n")
    artifacts.finish_candidate(project, run)
    (run / "report.json").write_text(json.dumps({"status": "success"}))
    (root / "added_test.cj").write_text("// New user acceptance must invalidate the receipt.\n")
    if operation == "apply":
        result = invoke(tmp_path, "apply", run)
        assert result.returncode == 2, result.output
        assert "inventory changed" in json.loads(result.output)["reason"]
    else:
        with pytest.raises(ValueError, match="inventory changed"):
            artifacts.replay_project(run, tools)
    assert (root / "lib.cj").read_text().endswith("value() { 1 }\n")


@pytest.mark.parametrize("operation", [artifacts.apply_candidate, artifacts.replay_project])
def test_editor_boundary_refuses_recorded_project_outside_workspace(
    tmp_path, monkeypatch, operation
):
    source, run = prepared(tmp_path)
    member = tmp_path / "member"
    member.mkdir()
    monkeypatch.setenv("INSPECT_CJ_WORKSPACE_ROOT", str(member))
    with pytest.raises(ValueError, match="Open and trust the containing workspace"):
        operation(run)
    assert source.read_text() == "main() { 1 }\n"
