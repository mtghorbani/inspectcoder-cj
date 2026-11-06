"""User configuration and portable toolchain behavior, independent of installed SDKs."""

import json
import os
import sys
from pathlib import Path

import pytest

from inspect_cj.cli import main
from inspect_cj.process import run_command
from inspect_cj.project import Toolchain


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    for name in (
        "CJC",
        "CJDB",
        "CJPM",
        "CANGJIE_HOME",
        "INSPECT_CJ_SYSROOT",
        "CJDB_PYTHON_FRAMEWORK",
        "INSPECT_CJ_CONFIG",
        "XDG_CONFIG_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "settings"))
    monkeypatch.setenv("PATH", str(tmp_path / "path"))
    monkeypatch.setattr("inspect_cj.project.platform.system", lambda: "Linux")


def executable(path, body="#!/bin/sh\nexit 0\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return str(path)


def sdk_at(root):
    executable(root / "bin/cjc")
    executable(root / "third_party/llvm/bin/lldb")
    executable(root / "tools/bin/cjpm")
    return root


def save(tmp_path, config):
    path = tmp_path / "settings/inspect-cj/config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config))
    return path


def test_configure_persists_literal_paths_and_merges_known_keys(tmp_path, capsys):
    path = save(tmp_path, {"cli": "/user tools/inspect-cj", "sysroot": "/old sdk"})
    before = dict(os.environ)
    assert main(["configure", "--sdk", "/selected sdk", "--sysroot", "/compatible sdk"]) == 0
    assert json.loads(path.read_text()) == {
        "cli": "/user tools/inspect-cj",
        "sdk": "/selected sdk",
        "sysroot": "/compatible sdk",
    }
    assert dict(os.environ) == before
    assert main(["configure"]) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["path"] == str(path)
    assert report["config"]["sdk"] == "/selected sdk"


def test_configure_alternate_file_is_atomic_and_private(tmp_path, monkeypatch):
    from inspect_cj import config

    target = tmp_path / "profile/config.json"
    monkeypatch.setenv("INSPECT_CJ_CONFIG", str(target))
    assert main(["configure", "--sdk", "/sdk"]) == 0
    original = target.read_bytes()
    assert target.stat().st_mode & 0o077 == 0

    def fail_replace(source, destination):
        raise OSError("simulated write failure")

    monkeypatch.setattr(config.os, "replace", fail_replace)
    assert main(["configure", "--sdk", "/other"]) == 2
    assert target.read_bytes() == original
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize(
    "content",
    [
        "{",
        "[]",
        '{"sdk":"relative"}',
        '{"sdk":""}',
        '{"sdk":null}',
        '{"sdk":123}',
        '{"sdk":"/a\\u0000b"}',
        '{"token":"private-secret"}',
        '{"sdk":"/a","sdk":"/b"}',
    ],
)
def test_invalid_config_fails_with_actionable_diagnostic(tmp_path, capsys, content):
    path = save(tmp_path, {})
    path.write_text(content)
    assert main(["configure"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert str(path) in report["reason"]
    assert "config" in report["reason"].lower()
    assert "private-secret" not in report["reason"]


def test_configure_rejects_relative_paths_without_overwriting(tmp_path):
    path = save(tmp_path, {"sdk": "/first"})
    assert main(["configure", "--sdk", "relative"]) == 2
    assert json.loads(path.read_text()) == {"sdk": "/first"}


def test_default_config_uses_home_and_override_is_literal(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert main(["configure", "--sdk", "/sdk"]) == 0
    assert (tmp_path / ".config/inspect-cj/config.json").is_file()
    target = tmp_path / "literal space/config.json"
    monkeypatch.setenv("INSPECT_CJ_CONFIG", str(target))
    assert main(["configure", "--sdk", "/other"]) == 0
    assert json.loads(target.read_text()) == {"sdk": "/other"}


def test_sdk_config_and_environment_tool_precedence(tmp_path, monkeypatch):
    selected = sdk_at(tmp_path / "selected sdk")
    path_sdk = sdk_at(tmp_path / "path sdk")
    monkeypatch.setenv("PATH", str(path_sdk / "bin"))
    save(tmp_path, {"sdk": str(selected), "sysroot": "/saved sdk"})
    tools = Toolchain.discover()
    assert tools.cjc == str(selected / "bin/cjc")
    assert tools.cjdb == str(selected / "third_party/llvm/bin/lldb")
    assert tools.cjpm == str(selected / "tools/bin/cjpm")
    assert tools.sysroot == "/saved sdk"
    override = executable(tmp_path / "custom/cjc")
    save(tmp_path, {"sdk": str(selected), "cjc": override, "sysroot": "/saved sdk"})
    assert Toolchain.discover().cjc == override
    monkeypatch.setenv("CJC", str(path_sdk / "bin/cjc"))
    monkeypatch.setenv("CJDB", str(path_sdk / "third_party/llvm/bin/lldb"))
    monkeypatch.setenv("CJPM", str(path_sdk / "tools/bin/cjpm"))
    monkeypatch.setenv("INSPECT_CJ_SYSROOT", "/environment sdk")
    tools = Toolchain.discover("/explicit sdk")
    assert tools.cjc == str(path_sdk / "bin/cjc")
    assert tools.cjdb == str(path_sdk / "third_party/llvm/bin/lldb")
    assert tools.cjpm == str(path_sdk / "tools/bin/cjpm")
    assert tools.sysroot == "/explicit sdk"
    assert Toolchain.discover().sysroot == "/environment sdk"


def test_sdk_home_and_path_discovery_without_personal_fallback(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError, match="configure --sdk"):
        Toolchain.discover()
    root = sdk_at(tmp_path / "SDK")
    monkeypatch.setenv("CANGJIE_HOME", str(root))
    assert Toolchain.discover().cjc == str(root / "bin/cjc")
    monkeypatch.delenv("CANGJIE_HOME")
    monkeypatch.setenv("PATH", str(root / "bin"))
    assert Toolchain.discover().cjdb == str(root / "third_party/llvm/bin/lldb")


def test_invalid_explicit_tool_fails_instead_of_falling_back(tmp_path, monkeypatch):
    root = sdk_at(tmp_path / "SDK")
    save(tmp_path, {"sdk": str(root), "cjc": str(tmp_path / "missing")})
    with pytest.raises(FileNotFoundError, match="cjc"):
        Toolchain.discover()
    save(tmp_path, {"sdk": str(root), "cjdb": str(tmp_path / "missing")})
    with pytest.raises(FileNotFoundError, match="CJDB"):
        Toolchain.discover()


def test_saved_framework_applies_only_to_debugger_children(tmp_path):
    root = sdk_at(tmp_path / "SDK")
    save(tmp_path, {"sdk": str(root), "python_framework": "/selected frameworks"})
    tools = Toolchain.discover()
    from unittest.mock import patch

    with patch("inspect_cj.project.platform.system", return_value="Darwin"):
        assert tools.environment(debugger=True)["DYLD_FRAMEWORK_PATH"] == "/selected frameworks"
        assert "DYLD_FRAMEWORK_PATH" not in tools.environment()


def test_automatic_macos_sysroot_and_python_metadata(tmp_path, monkeypatch):
    root = sdk_at(tmp_path / "SDK")
    framework = tmp_path / "installed python/Frameworks"
    framework.mkdir(parents=True)
    sysroot = tmp_path / "Apple SDK"
    sysroot.mkdir()
    binary = tmp_path / "path"
    executable(binary / "xcrun", f"#!/bin/sh\nprintf '%s' '{sysroot}'\n")
    executable(
        binary / "python3.11",
        '#!/bin/sh\nprintf \'%s\' \'{"version":[3,11],"framework":"' + str(framework) + "\"}'\n",
    )
    monkeypatch.setenv("PATH", str(binary))
    monkeypatch.setattr("inspect_cj.project.platform.system", lambda: "Darwin")
    save(tmp_path, {"sdk": str(root)})
    tools = Toolchain.discover()
    assert tools.sysroot == str(sysroot)
    assert tools.environment(debugger=True)["DYLD_FRAMEWORK_PATH"] == str(framework)


def test_configure_runs_as_installed_module_without_sdk(tmp_path, monkeypatch):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = run_command(
        [sys.executable, "-m", "inspect_cj", "configure", "--sdk", "/sdk with spaces"],
        cwd=tmp_path,
        env=env,
        timeout=5,
    )
    assert result.returncode == 0, result.output
    assert json.loads(result.output)["config"]["sdk"] == "/sdk with spaces"


def test_explicit_environment_compiler_uses_its_matching_sdk(tmp_path, monkeypatch):
    saved = sdk_at(tmp_path / "saved SDK")
    selected = sdk_at(tmp_path / "environment SDK")
    save(tmp_path, {"sdk": str(saved)})
    monkeypatch.setenv("CJC", str(selected / "bin/cjc"))
    tools = Toolchain.discover()
    assert tools.cjpm == str(selected / "tools/bin/cjpm")
    assert tools.cjdb == str(selected / "third_party/llvm/bin/lldb")
    assert tools.environment()["CANGJIE_HOME"] == str(selected)


@pytest.mark.parametrize("variable", ["INSPECT_CJ_CONFIG", "XDG_CONFIG_HOME"])
def test_relative_configuration_locations_fail(tmp_path, monkeypatch, capsys, variable):
    monkeypatch.setenv(variable, "relative")
    assert main(["configure"]) == 2
    assert variable in json.loads(capsys.readouterr().out)["reason"]


def test_missing_explicit_config_is_not_silently_ignored(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "missing.json"
    monkeypatch.setenv("INSPECT_CJ_CONFIG", str(missing))
    assert main(["configure"]) == 2
    assert "configure --sdk" in json.loads(capsys.readouterr().out)["reason"]
    assert main(["configure", "--sdk", "/selected sdk"]) == 0
    assert json.loads(missing.read_text())["sdk"] == "/selected sdk"


def test_macos_missing_platform_tools_does_not_add_personal_framework(tmp_path, monkeypatch):
    selected = sdk_at(tmp_path / "SDK")
    save(tmp_path, {"sdk": str(selected)})
    monkeypatch.setattr("inspect_cj.project.platform.system", lambda: "Darwin")
    tools = Toolchain.discover()
    assert tools.sysroot is None
    assert "DYLD_FRAMEWORK_PATH" not in tools.environment(debugger=True)


def test_malformed_config_prevents_toolchain_fallback(tmp_path):
    save(tmp_path, {"token": "do-not-print"})
    with pytest.raises(ValueError, match="unknown setting"):
        Toolchain.discover()


@pytest.mark.parametrize("command", ["configure", "doctor"])
def test_config_special_file_fails_promptly(tmp_path, command):
    fifo = tmp_path / "config.fifo"
    os.mkfifo(fifo)
    env = dict(
        os.environ,
        INSPECT_CJ_CONFIG=str(fifo),
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    result = run_command(
        [sys.executable, "-m", "inspect_cj", command],
        cwd=tmp_path,
        env=env,
        timeout=1,
    )
    assert not result.timed_out, "user config must reject FIFOs before attempting a blocking read"
    assert result.returncode == 2
    assert "regular file" in json.loads(result.output)["reason"]


@pytest.mark.parametrize("character,length", [("x", 24000), ("é", 12000)])
def test_configure_rejects_oversized_serialization_without_replacing_config(
    tmp_path, character, length
):
    path = save(tmp_path, {"sdk": "/valid SDK", "cli": "/valid CLI"})
    original = path.read_bytes()
    oversized_path = "/" + character * length
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = run_command(
        [
            sys.executable,
            "-m",
            "inspect_cj",
            "configure",
            "--sdk",
            oversized_path,
            "--cjc",
            oversized_path,
            "--cjdb",
            oversized_path,
        ],
        cwd=tmp_path,
        env=env,
        timeout=5,
        output_limit=4096,
    )
    assert result.returncode == 2
    report = json.loads(result.output)
    assert "64KiB" in report["reason"]
    assert str(path) in report["reason"]
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]
    reread = run_command(
        [sys.executable, "-m", "inspect_cj", "configure"], cwd=tmp_path, env=env, timeout=5
    )
    assert reread.returncode == 0, reread.output
    assert json.loads(reread.output)["config"] == {"sdk": "/valid SDK", "cli": "/valid CLI"}
