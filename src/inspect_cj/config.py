"""Strict user-owned configuration shared with the native clients."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

KEYS = ("sdk", "cjc", "cjdb", "cjpm", "sysroot", "python_framework", "cli")


def absolute_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and "\0" not in value
        and Path(value).is_absolute()
    )


def config_path() -> Path:
    override = os.environ.get("INSPECT_CJ_CONFIG")
    if override is not None:
        if not absolute_path(override):
            raise ValueError(
                "INSPECT_CJ_CONFIG must be a nonempty absolute configuration file path"
            )
        return Path(override)
    directory = os.environ.get("XDG_CONFIG_HOME")
    if directory is not None and not absolute_path(directory):
        raise ValueError("XDG_CONFIG_HOME must be a nonempty absolute directory path")
    return (Path(directory) if directory else Path.home() / ".config") / "inspect-cj/config.json"


def validate(values: object, path: Path) -> dict[str, str]:
    if not isinstance(values, dict):
        raise ValueError(f"Invalid config {path}: expected a JSON object; edit or remove this file")
    if any(key not in KEYS for key in values):
        raise ValueError(f"Invalid config {path}: unknown setting; allowed keys: {', '.join(KEYS)}")
    for key, value in values.items():
        if not absolute_path(value):
            raise ValueError(
                f"Invalid config {path}: {key} must be a nonempty absolute path without NUL"
            )
    return values


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result


def load_config(path: Path | None = None, *, missing_ok: bool | None = None) -> dict[str, str]:
    path = path if path is not None else config_path()
    if missing_ok is None:
        missing_ok = "INSPECT_CJ_CONFIG" not in os.environ
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError(
                    f"Invalid config {path}: expected a regular file; replace this file"
                )
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                contents = stream.read(65537)
        finally:
            os.close(descriptor)
    except FileNotFoundError:
        if missing_ok:
            return {}
        raise ValueError(
            f"Config {path} does not exist; create it with inspect-cj configure --sdk PATH"
        ) from None
    except OSError as error:
        raise ValueError(
            f"Cannot read config {path}; check file permissions ({error.strerror})"
        ) from None
    if len(contents) > 65536:
        raise ValueError(f"Invalid config {path}: maximum size is 64KiB; edit this file")
    try:
        values = json.loads(contents.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError(
            f"Invalid config {path}: use valid JSON with unique keys; edit or remove this file"
        ) from None
    return validate(values, path)


def write_config(updates: dict[str, str]) -> tuple[Path, dict[str, str]]:
    path = config_path()
    validate(updates, path)
    values = {**load_config(path, missing_ok=True), **updates}
    contents = (json.dumps(values, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(contents) > 65536:
        raise ValueError(
            f"Config {path} would exceed the maximum size of 64KiB; shorten configured paths"
        )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".config-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path, values
