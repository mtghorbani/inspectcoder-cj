"""Bounded process execution; every launched process group has an owner."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


class OutputBuffer:
    """Keep the beginning and end without accumulating discarded output."""

    def __init__(self, limit: int = 65536):
        if limit < 64:
            raise ValueError("output limit must be at least 64 bytes")
        self.limit = limit
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def append(self, chunk: bytes) -> None:
        self.total += len(chunk)
        head_size = self.limit // 2
        take = min(len(chunk), head_size - len(self.head))
        self.head.extend(chunk[:take])
        self.tail.extend(chunk[take:])
        del self.tail[: max(0, len(self.tail) - (self.limit - head_size))]

    def text(self) -> str:
        omitted = self.total - len(self.head) - len(self.tail)
        marker = f"\n[... omitted {omitted} bytes ...]\n".encode() if omitted else b""
        return (self.head + marker + self.tail).decode("utf-8", errors="replace")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    output: str
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_truncated: bool = False
    stderr_truncated: bool = False


def kill_group(process: subprocess.Popen) -> None:
    """Kill owned descendants as well as the direct child, then reap the child."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # A killed LLDB's helper group can briefly be inaccessible. Reap the known
        # exited child; permission failures for a live owned child still propagate.
        if process.poll() is None:
            raise
    process.wait(timeout=5)


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    input_text: str = "",
    output_limit: int = 65536,
    env: dict[str, str] | None = None,
    separate_streams: bool = False,
) -> CommandResult:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    output = OutputBuffer(output_limit)
    stdout = OutputBuffer(output_limit)
    stderr = OutputBuffer(output_limit)
    deadline = time.monotonic() + timeout
    timed_out = False
    # A file avoids deadlocking on a large stdin pipe and ensures EOF for the program.
    with tempfile.TemporaryFile() as stdin, selectors.DefaultSelector() as selector:
        stdin.write(input_text.encode())
        stdin.seek(0)
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if separate_streams else subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        assert process.stdout is not None
        selector.register(process.stdout, selectors.EVENT_READ, stdout)
        if process.stderr is not None:
            selector.register(process.stderr, selectors.EVENT_READ, stderr)
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 8192)
                    if chunk:
                        output.append(chunk)
                        key.data.append(chunk)
                    else:
                        selector.unregister(key.fileobj)
                if process.poll() is not None:
                    # Drain only available bytes; descendants cannot hold us open forever.
                    while selector.get_map():
                        ready = selector.select(0)
                        if not ready:
                            break
                        if time.monotonic() >= deadline:
                            timed_out = True
                            break
                        for key, _ in ready:
                            chunk = os.read(key.fd, 8192)
                            if not chunk:
                                selector.unregister(key.fileobj)
                            else:
                                output.append(chunk)
                                key.data.append(chunk)
                    break
            if process.poll() is None and not timed_out:
                try:
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
        finally:
            kill_group(process)
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
    return CommandResult(
        process.returncode,
        output.text(),
        timed_out,
        stdout.text(),
        stderr.text(),
        stdout.total,
        stderr.total,
        stdout.total > output_limit,
        stderr.total > output_limit,
    )


def truncate(text: str, limit: int = 16000) -> str:
    buffer = OutputBuffer(limit)
    buffer.append(text.encode())
    return buffer.text()
