"""Public release preflight executes the publishing contract without uploading."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_check(config):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/release.py"), "check", "--config", str(config)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_release_preflight_accepts_public_identities():
    result = run_check(ROOT / "release.json")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["version"] == "0.2.0"
    assert report["vscode_id"] == "mtghorbani.inspectcoder-cj"
    assert report["npm_name"] == "@mtghorbani/inspectcoder-cj"
    assert report["publication_performed"] is False


def test_release_preflight_refuses_placeholder_identity(tmp_path):
    data = json.loads((ROOT / "release.json").read_text())
    data["vscode_publisher"] = "YOUR_PUBLISHER"
    config = tmp_path / "release.json"
    config.write_text(json.dumps(data))
    result = run_check(config)
    assert result.returncode == 2
    assert "publisher" in json.loads(result.stdout)["reason"].lower()


def test_release_preflight_refuses_repository_credentials(tmp_path):
    data = json.loads((ROOT / "release.json").read_text())
    data["repository"] = "https://secret@example.com/project"
    config = tmp_path / "release.json"
    config.write_text(json.dumps(data))
    result = run_check(config)
    assert result.returncode == 2
    assert "secret" not in result.stdout
    assert "repository" in json.loads(result.stdout)["reason"].lower()


def test_release_preflight_refuses_unknown_options(tmp_path):
    data = json.loads((ROOT / "release.json").read_text())
    data["token"] = "private-value"
    config = tmp_path / "release.json"
    config.write_text(json.dumps(data))
    result = run_check(config)
    assert result.returncode == 2
    assert "private-value" not in result.stdout

    assert "identities" in json.loads(result.stdout)["reason"]


def test_vsix_inspection_accepts_vsce_normalized_license_name(tmp_path):
    import runpy
    import zipfile

    release = runpy.run_path(str(ROOT / "scripts/release.py"))
    archive = tmp_path / "extension.vsix"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(
            "extension/package.json", json.dumps({"publisher": "owner", "name": "tool"})
        )
        package.writestr("extension/LICENSE.txt", "MIT License")
        package.writestr("extension/NOTICE.md", "attribution")
        package.writestr("extension/runtime/cli.cjs", "module.exports = {}")
    release["inspect_artifact"](archive, {"vscode_id": "owner.tool"})
