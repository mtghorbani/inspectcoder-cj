#!/usr/bin/env python3
"""Build and inspect local release artifacts. This command never publishes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CONFIG_KEYS = {"repository", "vscode_publisher", "vscode_name", "npm_name", "python_name"}


def read_json(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Release configuration must be a regular JSON file under 64KiB")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Release configuration must be a JSON object")
    return value


def preflight(root: Path, config: Path) -> dict:
    identity = read_json(config)
    if set(identity) != CONFIG_KEYS or any(not isinstance(v, str) for v in identity.values()):
        raise ValueError(
            "Release configuration requires repository and all four package identities"
        )
    url = urlsplit(identity["repository"])
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or not url.path.strip("/")
        or url.hostname in {"example.com", "localhost"}
        or "YOUR" in identity["repository"]
    ):
        raise ValueError(
            "Repository must be a public HTTPS URL without credentials or placeholders"
        )
    for key in ("vscode_publisher", "vscode_name", "python_name"):
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", identity[key]) or identity[key].startswith(
            "your-"
        ):
            raise ValueError(f"Select a real {key.replace('_', ' ')} before preparing a release")
    if not re.fullmatch(r"@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*", identity["npm_name"]):
        raise ValueError("Select a real scoped npm package name, such as @owner/inspectcoder-cj")
    python = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    agent = read_json(root / "plugins/agent-tools/package.json")
    vscode = read_json(root / "editors/vscode/package.json")
    version = python["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or any(
        p["version"] != version for p in (agent, vscode)
    ):
        raise ValueError(
            "Python, npm and VS Code versions must match using three numeric components"
        )
    if not (root / "LICENSE").is_file() or python.get("license") != "MIT":
        raise ValueError("The original code must include its selected MIT license")
    if any(p.get("license") != "MIT" for p in (agent, vscode)):
        raise ValueError("All packages must declare the selected MIT license")
    if (
        python["name"] != identity["python_name"]
        or agent["name"] != identity["npm_name"]
        or vscode["publisher"] != identity["vscode_publisher"]
        or vscode["name"] != identity["vscode_name"]
    ):
        raise ValueError("release.json identities must match the package manifests")
    if any(
        p.get("repository", {}).get("url", "").removesuffix(".git") != identity["repository"]
        for p in (agent, vscode)
    ):
        raise ValueError("Package repository URLs must match release.json")
    return {
        **identity,
        "version": version,
        "vscode_id": vscode["publisher"] + "." + vscode["name"],
        "publication_performed": False,
    }


def run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, stdout=sys.stderr)


def inspect_artifact(path: Path, info: dict) -> None:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not any(n.endswith("/licenses/LICENSE") for n in names):
                raise ValueError("Wheel is missing its license")
            if "inspect_cj/cli.py" not in names or "inspect_cj/config.py" not in names:
                raise ValueError("Wheel is missing the portable CLI")
    elif path.suffix == ".vsix":
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("extension/package.json"))
            if manifest["publisher"] + "." + manifest["name"] != info["vscode_id"]:
                raise ValueError("VSIX publisher identity differs from preflight")
            if not {"extension/LICENSE", "extension/LICENSE.txt"}.intersection(archive.namelist()):
                raise ValueError("VSIX is missing its MIT license")
            for name in ("extension/runtime/cli.cjs", "extension/NOTICE.md"):
                archive.getinfo(name)
    elif path.suffix == ".tgz":
        with tarfile.open(path) as archive:
            names = archive.getnames()
            manifest = json.load(archive.extractfile("package/package.json"))
            if manifest["name"] != info["npm_name"]:
                raise ValueError("npm package identity differs from preflight")
            for name in (
                "package/dist/opencode.js",
                "package/dist/pi.js",
                "package/runtime/cli.cjs",
                "package/LICENSE",
                "package/NOTICE.md",
            ):
                if name not in names:
                    raise ValueError(f"npm package missing {name}")
            if any("/node_modules/" in n or "/test/" in n for n in names):
                raise ValueError("npm package contains development payload")
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path) as archive:
            names = archive.getnames()
            roots = {name.split("/", 1)[0] for name in names}
            if len(roots) != 1:
                raise ValueError("Source archive must have one package root")
            prefix = roots.pop()
            for name in ("LICENSE", "NOTICE.md", "pyproject.toml", "src/inspect_cj/config.py"):
                if f"{prefix}/{name}" not in names:
                    raise ValueError(f"Source archive missing {name}")


def build(root: Path, output: Path, info: dict) -> dict:
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Release output directory must be empty; choose a new output path")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="inspectcoder-cj-release-") as temporary:
        stage = Path(temporary) / "source"
        stage.mkdir()
        ignored = shutil.ignore_patterns(
            "node_modules",
            "__pycache__",
            "*.pyc",
            "*.egg-info",
            "dist",
            "build",
            "target",
            ".cjpm",
            ".inspect-cj",
            "*.vsix",
        )
        for name in ("src", "runtime", "scripts", "tests", "examples", "editors", "plugins"):
            shutil.copytree(root / name, stage / name, ignore=ignored)
        for name in (
            "pyproject.toml",
            "MANIFEST.in",
            "README.md",
            "GETTING_STARTED.md",
            "LICENSE",
            "NOTICE.md",
            "release.json",
        ):
            shutil.copy2(root / name, stage / name)
        run([sys.executable, "-m", "build", "--outdir", str(output)], stage)
        run(
            [
                sys.executable,
                "-m",
                "twine",
                "check",
                *map(str, output.glob("*.whl")),
                *map(str, output.glob("*.tar.gz")),
            ],
            stage,
        )
        for directory in (stage / "plugins/agent-tools", stage / "editors/vscode"):
            run(["npm", "ci", "--ignore-scripts"], directory)
        run(["npm", "pack", "--pack-destination", str(output)], stage / "plugins/agent-tools")
        run(
            [
                "npm",
                "run",
                "package",
                "--",
                "--out",
                str(output / f"{info['vscode_name']}-{info['version']}.vsix"),
            ],
            stage / "editors/vscode",
        )
    artifacts = sorted(p for p in output.iterdir() if p.is_file())
    if len(artifacts) != 4:
        raise ValueError("Expected exactly wheel, source archive, npm tarball and VSIX")
    for artifact in artifacts:
        inspect_artifact(artifact, info)
    checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts}
    (output / "SHA256SUMS").write_text(
        "".join(f"{value}  {name}\n" for name, value in checksums.items())
    )
    manifest = {**info, "artifacts": checksums, "build_python": sys.version.split()[0]}
    (output / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "build"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        info = preflight(root, args.config or root / "release.json")
        result = (
            info
            if args.command == "check"
            else build(root, args.output or root / "dist/release" / info["version"], info)
        )
        print(json.dumps({"status": "success", **result}))
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        # Do not echo release configuration contents, which may contain a mistakenly pasted token.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": str(error)
                    if isinstance(error, ValueError)
                    else "Release preparation failed; inspect the preceding build diagnostics",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
