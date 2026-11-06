"""End-user contract failures, independent of model or SDK availability."""

import json
import os
import sys
from pathlib import Path

import pytest

from inspect_cj.process import run_command


def invoke(tmp_path, *args, stdin=""):
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    return run_command(
        [sys.executable, "-m", "inspect_cj", *map(str, args)],
        cwd=tmp_path,
        timeout=60,
        input_text=stdin,
        env=env,
    )


def test_source_without_oracle_returns_missing_input_without_model(tmp_path):
    source = tmp_path / "main.cj"
    source.write_text('main() { println("bug"); 0 }')
    result = invoke(
        tmp_path, "repair", source, "--task", "Print correct answer", "--non-interactive"
    )
    assert result.returncode == 5, result.output
    report = json.loads(result.output)
    assert report["status"] == "needs_input"
    assert "expectation" in report["missing"]
    assert source.read_text() == 'main() { println("bug"); 0 }'


def test_directory_does_not_guess_between_sources(tmp_path):
    (tmp_path / "one.cj").write_text("main() { 0 }")
    (tmp_path / "two.cj").write_text("main() { 0 }")
    result = invoke(tmp_path, "repair", tmp_path, "--expect-line", "ok", "--non-interactive")
    assert result.returncode == 5, result.output
    assert "target" in json.loads(result.output)["missing"]


def test_task_and_program_stdin_cannot_both_consume_cli_stdin(tmp_path):
    source = tmp_path / "main.cj"
    source.write_text("main() { 0 }")
    result = invoke(tmp_path, "repair", source, "--task", "-", "--stdin-file", "-", stdin="text")
    assert result.returncode == 2, result.output
    assert "mutually exclusive" in json.loads(result.output)["reason"]


@pytest.mark.native
@pytest.mark.skipif(os.environ.get("INSPECT_CJ_NATIVE") != "1", reason="requires native compiler")
def test_native_source_argv_records_identity_and_replay_rejects_changed_source(tmp_path):
    source = tmp_path / "main.cj"
    original = "main(args: Array<String>) { println(args[0]); 0 }\n"
    source.write_text(original)
    script = tmp_path / "script.json"
    script.write_text("[]")
    output = tmp_path / "run"
    result = invoke(
        tmp_path,
        "repair",
        source,
        "--task",
        "Echo first arg",
        "--expect-line",
        "ok",
        "--output",
        output,
        "--transport",
        "scripted",
        "--script",
        script,
        "--",
        "ok",
    )
    assert result.returncode == 0, result.output
    assert source.read_text() == original
    record = json.loads((output / "invocation.json").read_text())
    assert record["config"]["tests"][0]["args"] == ["ok"]
    assert record["sources"]["main.cj"]["sha256"]
    assert record["toolchain"]["cjc"]["sha256"]
    assert (output / "project.json").is_file()
    source.write_text(original + "// user change\n")
    replay = invoke(
        tmp_path,
        "repair",
        "--replay",
        output,
        "--transport",
        "scripted",
        "--script",
        script,
        "--output",
        tmp_path / "second",
    )
    assert replay.returncode == 2, replay.output
    assert "changed" in json.loads(replay.output)["reason"]
    assert not (tmp_path / "second").exists()


@pytest.mark.native
@pytest.mark.skipif(os.environ.get("INSPECT_CJ_NATIVE") != "1", reason="requires native compiler")
def test_native_task_stdin_and_immutable_driver(tmp_path):
    source = tmp_path / "function.cj"
    source.write_text("func answer(): Int64 { 42 }\n")
    driver = tmp_path / "driver.cj"
    driver.write_text("main() { println(answer()); 0 }\n")
    script = tmp_path / "script.json"
    script.write_text("[]")
    result = invoke(
        tmp_path,
        "repair",
        source,
        "--driver",
        driver,
        "--task",
        "-",
        "--expect-line",
        "42",
        "--output",
        tmp_path / "run",
        "--transport",
        "scripted",
        "--script",
        script,
        stdin="Return the answer\n",
    )
    assert result.returncode == 0, result.output
    assert driver.read_text() == "main() { println(answer()); 0 }\n"
    assert json.loads((tmp_path / "run/project.json").read_text())["requirement"] == (
        "Return the answer\n"
    )


@pytest.mark.native
@pytest.mark.skipif(os.environ.get("INSPECT_CJ_NATIVE") != "1", reason="requires native compiler")
def test_native_compile_failure_repairs_then_validates_independent_output(tmp_path):
    source = tmp_path / "main.cj"
    original = 'main() { let value: Int64 = "bad"; println(value); 0 }\n'
    source.write_text(original)
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            [
                {
                    "role": "coder",
                    "response": {
                        "source": "main() { println(42); 0 }\n",
                        "summary": "Use the expected integer",
                    },
                }
            ]
        )
    )
    output = tmp_path / "run"
    result = invoke(
        tmp_path,
        "repair",
        source,
        "--expect-line",
        "42",
        "--output",
        output,
        "--transport",
        "scripted",
        "--script",
        script,
    )
    assert result.returncode == 0, result.output
    report = json.loads((output / "report.json").read_text())
    assert report["patch_attempts"] == 1
    assert report["iterations"][0]["build"]["returncode"] != 0
    assert report["iterations"][-1]["tests"][0]["passed"]
    events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
    assert [event["role"] for event in events if event["type"] == "model"] == ["coder"]
    assert source.read_text() == original


@pytest.mark.native
@pytest.mark.skipif(os.environ.get("INSPECT_CJ_NATIVE") != "1", reason="requires native compiler")
def test_native_compile_only_is_not_semantic_success(tmp_path):
    source = tmp_path / "main.cj"
    source.write_text("main() { 0 }\n")
    script = tmp_path / "script.json"
    script.write_text("[]")
    result = invoke(
        tmp_path,
        "repair",
        source,
        "--compile-only",
        "--output",
        tmp_path / "run",
        "--transport",
        "scripted",
        "--script",
        script,
    )
    assert result.returncode == 0, result.output
    assert json.loads(result.output)["status"] == "compile_only"


def test_scratch_driver_requires_independent_expectation_and_never_overwrites(tmp_path):
    path = tmp_path / "repro.cj"
    missing = invoke(tmp_path, "driver", "--expression", "answer()", "--output", path)
    assert missing.returncode == 5, missing.output
    assert not path.exists()
    generated = invoke(
        tmp_path, "driver", "--expression", "answer()", "--expect-line", "42", "--output", path
    )
    assert generated.returncode == 0, generated.output
    assert "println(answer())" in path.read_text()
    before = path.read_bytes()
    repeated = invoke(
        tmp_path, "driver", "--expression", "wrong()", "--expect-line", "0", "--output", path
    )
    assert repeated.returncode == 2
    assert path.read_bytes() == before


def test_noninteractive_unknown_option_is_machine_readable(tmp_path):
    result = invoke(tmp_path, "repair", "main.cj", "--non-interactive", "--not-supported")
    assert result.returncode == 2
    assert json.loads(result.output)["status"] == "failed"


def test_replay_refuses_native_selection_override(tmp_path):
    result = invoke(tmp_path, "repair", "--replay", tmp_path / "absent", "--case", "cjpm:any")
    assert result.returncode == 2, result.output
    assert "cannot override" in json.loads(result.output)["reason"]


@pytest.mark.parametrize("option", ["--driver", "--stdin-file"])
def test_editor_boundary_refuses_external_standalone_inputs(tmp_path, monkeypatch, option):
    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "main.cj"
    source.write_text("main() { 0 }\n")
    external = tmp_path / "external.cj"
    external.write_text("independent input")
    monkeypatch.setenv("INSPECT_CJ_WORKSPACE_ROOT", str(root))
    result = invoke(tmp_path, "repair", source, "--expect-exit", "0", option, external)
    assert result.returncode != 0
    assert "Open and trust the containing workspace" in json.loads(result.output)["reason"]


@pytest.mark.native
@pytest.mark.skipif(os.environ.get("INSPECT_CJ_NATIVE") != "1", reason="requires native compiler")
@pytest.mark.parametrize("expected", ["42\n", "43\n"])
def test_compile_first_coder_receives_canonical_cases(tmp_path, expected):
    from inspect_cj.project import Project
    from inspect_cj.repair import RepairController

    source = tmp_path / "main.cj"
    original = 'main() { let value: Int64 = "bad"; println(value); 0 }\n'
    source.write_text(original)
    contract = {
        "name": "declared",
        "args": ["argument"],
        "stdin": "input\n",
        "stdout": expected,
        "stderr": "",
        "exit_code": 0,
        "comparison": "exact",
    }
    project = Project.from_config(
        tmp_path,
        {
            "sources": ["main.cj"],
            "editable": "main.cj",
            "requirement": "Preserve declared behavior",
            "tests": [contract],
        },
    )
    requests = []

    class ContractCoder:
        label = "contract-coder"

        def complete(self, role, context, *, timeout=120):
            requests.append((role, context))
            assert role == "coder"
            assert context["mode"] == "compiler_diagnostics"
            assert context["failure"] is None
            assert context.get("tests") == [contract]
            return {
                "source": f"main() {{ print({json.dumps(context['tests'][0]['stdout'])}); 0 }}\n",
                "summary": "Implement declared output",
            }

    result = RepairController(project, ContractCoder(), tmp_path / "run", max_attempts=1).run()
    assert result.status == "success", result
    assert len(requests) == 1
    assert source.read_text() == original
