import sys

import pytest

from inspect_cj.process import run_command
from inspect_cj.project import Project, run_suite


def test_output_flood_retains_first_and_last(tmp_path):
    command = [sys.executable, "-c", 'print("FIRST" + "x"*200000 + "LAST")']
    result = run_command(command, cwd=tmp_path, timeout=3, output_limit=1024)
    assert result.returncode == 0
    assert result.output.startswith("FIRST")
    assert result.output.endswith("LAST\n")
    assert "omitted" in result.output
    assert len(result.output) < 1200


def test_timeout_terminates_process_group(tmp_path):
    pidfile = tmp_path / "pid"
    code = (
        "import subprocess,time; "
        f'p=subprocess.Popen(["sleep","30"]); open({str(pidfile)!r},"w").write(str(p.pid)); '
        "time.sleep(30)"
    )
    result = run_command([sys.executable, "-c", code], cwd=tmp_path, timeout=0.3)
    assert result.timed_out
    pid = int(pidfile.read_text())
    # A killed child can briefly remain a zombie until launchd reaps it.
    check = run_command(["ps", "-o", "stat=", "-p", str(pid)], cwd=tmp_path, timeout=2)
    assert check.returncode != 0 or check.output.strip().startswith("Z")


def test_manifest_rejects_escape_empty_suite_and_duplicate_names(tmp_path):
    import json

    (tmp_path / "main.cj").write_text("main() { 0 }")
    base = {
        "sources": ["main.cj"],
        "editable": "main.cj",
        "requirement": "return zero",
        "tests": [{"name": "zero", "stdout": ""}],
    }
    manifest = tmp_path / "project.json"
    for changed in ({"sources": ["../outside.cj"]}, {"tests": []}, {"tests": base["tests"] * 2}):
        manifest.write_text(json.dumps(base | changed))
        with pytest.raises(ValueError):
            Project.load(manifest)


def test_suite_runs_every_case_and_selects_first_failure(tmp_path):
    import json

    manifest = tmp_path / "project.json"
    (tmp_path / "main.cj").write_text("main() { 0 }")
    manifest.write_text(
        json.dumps(
            {
                "sources": ["main.cj"],
                "editable": "main.cj",
                "requirement": "echo",
                "tests": [
                    {"name": "first", "args": ["a"], "stdout": "wrong"},
                    {"name": "second", "args": ["b"], "stdout": "b\n"},
                ],
            }
        )
    )
    executable = tmp_path / "echo"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$1"\n')
    executable.chmod(0o755)
    suite = run_suite(Project.load(manifest), executable)
    assert [case.passed for case in suite] == [False, True]
    assert suite[0].name == "first"
    assert suite[0].actual_stdout == "a\n"


def test_manifest_rejects_external_source_symlink(tmp_path):
    import json

    outside = tmp_path.parent / (tmp_path.name + "-external.cj")
    outside.write_text("main() { 0 }")
    (tmp_path / "main.cj").symlink_to(outside)
    (tmp_path / "project.json").write_text(
        json.dumps(
            {
                "sources": ["main.cj"],
                "editable": "main.cj",
                "requirement": "zero",
                "tests": [{"name": "zero", "stdout": ""}],
            }
        )
    )
    with pytest.raises(ValueError):
        Project.load(tmp_path / "project.json")


def test_exited_parent_cannot_keep_deadline_open_with_flooding_descendants(tmp_path):
    import signal
    import time

    writer = 'import os\nwhile True: os.write(1, b"x" * 65536)'
    parent = (
        "import subprocess, sys, time\n"
        f'for _ in range(16): subprocess.Popen([sys.executable, "-c", {writer!r}])\n'
        "time.sleep(0.1)\n"
    )

    def safety_deadline(*_args):
        raise AssertionError("descendant output bypassed the configured process deadline")

    old_handler = signal.signal(signal.SIGALRM, safety_deadline)
    try:
        for _ in range(3):
            signal.setitimer(signal.ITIMER_REAL, 2)
            started = time.monotonic()
            result = run_command(
                [sys.executable, "-c", parent], cwd=tmp_path, timeout=0.2, output_limit=1024
            )
            signal.setitimer(signal.ITIMER_REAL, 0)
            assert time.monotonic() - started < 0.8
            assert len(result.output) < 1200
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def test_separate_capture_cannot_match_expected_stdout_using_stderr(tmp_path):
    result = run_command(
        [sys.executable, "-c", 'import sys; print("out"); print("err", file=sys.stderr)'],
        cwd=tmp_path,
        timeout=3,
        separate_streams=True,
    )
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
    assert result.stdout_bytes == 4 and result.stderr_bytes == 4
    assert not result.stdout_truncated and not result.stderr_truncated


def test_separate_match_rejects_expected_stdout_written_to_stderr(tmp_path):
    import json

    (tmp_path / "main.cj").write_text("main() { 0 }")
    manifest = tmp_path / "project.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": ["main.cj"],
                "editable": "main.cj",
                "requirement": "only stdout",
                "tests": [
                    {"name": "separate", "stdout": "ok\n", "stderr": "", "comparison": "exact"}
                ],
            }
        )
    )
    executable = tmp_path / "program"
    executable.write_text('#!/bin/sh\nprintf "ok\\n" >&2\n')
    executable.chmod(0o755)
    result = run_suite(Project.load(manifest), executable)[0]
    assert not result.passed
    assert result.actual_stdout == ""
    assert result.actual_stderr == "ok\n"


def test_separate_exit_expectation_rejects_truncated_unchecked_stream(tmp_path):
    from inspect_cj.project import TestCase as Case

    executable = tmp_path / "program"
    executable.write_text('#!/bin/sh\nhead -c 70000 /dev/zero | tr "\\000" x\n')
    executable.chmod(0o755)
    project = Project(
        tmp_path,
        [],
        "unused.cj",
        "bounded output",
        [
            Case("bounded", [], "", None, 0, None, "exact"),
        ],
    )
    result = run_suite(project, executable)[0]
    assert not result.passed
    assert result.truncated and result.stdout_bytes == 70000


def test_explicit_toolchain_exposes_matching_linker_to_child(tmp_path, monkeypatch):
    from inspect_cj.project import Toolchain

    sdk = tmp_path / "selected sdk"
    (sdk / "bin").mkdir(parents=True)
    llvm = sdk / "third_party/llvm/bin"
    llvm.mkdir(parents=True)
    linker = llvm / "ld64.lld"
    linker.write_text("#!/bin/sh\nprintf 'selected-sdk-linker'\n")
    linker.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    tools = Toolchain(str(sdk / "bin/cjc"), str(llvm / "lldb"), None)
    result = run_command(
        ["ld64.lld", "--version"], cwd=tmp_path, timeout=5, env=tools.environment()
    )
    assert result.returncode == 0
    assert result.output == "selected-sdk-linker"
