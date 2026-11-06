"""Stateful, validated model actions over a persistent native CJDB child."""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from .process import OutputBuffer, kill_group
from .project import Project, TestCase, Toolchain, positive

State = Literal["Start", "RuntimeState", "RuntimeError", "PostMortem", "Done"]
Status = Literal["ok", "invalid", "unsupported", "error", "timeout"]


@dataclass(frozen=True)
class Observation:
    id: str
    status: Status
    state: State
    output: str
    frames: list[dict] = field(default_factory=list)
    breakpoints: list[dict] = field(default_factory=list)
    live: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class CJDBSession:
    def __init__(
        self,
        project: Project,
        executable: Path,
        case: TestCase,
        *,
        tools: Toolchain | None = None,
        timeout: float = 10,
        deadline: float | None = None,
        launch: dict | None = None,
    ):
        self.project, self.executable, self.case = project, executable, case
        self.tools = tools or Toolchain.discover(project.sysroot)
        self.timeout = positive(timeout, "debug timeout")
        self.deadline = deadline
        self.launch = launch or {}
        self.state: State = "Start"
        self.context: dict = {"frames": [], "breakpoints": [], "mode": "start"}
        self.capabilities = {"inspection": True, "mutation": "unknown", "expression": "unknown"}
        self._sequence = 0
        self._inferior_group: int | None = None
        self._launch_request: int | None = None
        self._closed = True
        self._temporary = tempfile.TemporaryDirectory(prefix="inspect-cj-debug-")
        self.stdin_file = Path(self._temporary.name) / "stdin"
        self.stdin_file.write_text(case.stdin)
        self._start()

    def _start(self) -> None:
        self._socket, child = socket.socketpair()
        child.set_inheritable(True)
        self._socket.settimeout(self.timeout + 5)
        self._stream = self._socket.makefile("rwb", buffering=0)
        bridge = Path(__file__).with_name("cjdb_bridge.py")
        args = [
            self.tools.cjdb,
            "--no-lldbinit",
            "-b",
            "-Q",
            "-o",
            f'command script import "{bridge}"',
            "-o",
            f"script cjdb_bridge.serve({child.fileno()})",
            "-o",
            "quit",
        ]
        self._logs = OutputBuffer(8192)
        self._process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=self.tools.environment(debugger=True),
            pass_fds=(child.fileno(),),
            start_new_session=True,
        )
        child.close()
        self._closed = False

        def drain_logs():
            assert self._process.stdout is not None
            try:
                while chunk := self._process.stdout.read(4096):
                    self._logs.append(chunk)
            except (OSError, ValueError):
                pass

        self._reader = threading.Thread(target=drain_logs, daemon=True)
        self._reader.start()
        try:
            response = self._request("initialize", executable=str(self.executable.resolve()))
            if not response["ok"]:
                raise RuntimeError(response["output"])
            self.version = response["output"]
        except BaseException:
            self.close()
            raise

    def _request(self, operation: str, **parameters) -> dict:
        deadline = getattr(self, "deadline", None)
        if operation != "close" and deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("shared run deadline exhausted")
            self._socket.settimeout(min(self.timeout + 5, remaining))
            if "timeout" in parameters:
                parameters["timeout"] = min(parameters["timeout"], remaining)
        self._sequence += 1
        if operation == "continue":
            self._launch_request = self._sequence
        request = {"id": self._sequence, "operation": operation, **parameters}
        # Raw SocketIO.write may return a short count for large editor environments.
        self._socket.sendall((json.dumps(request) + "\n").encode())
        while True:
            line = self._stream.readline(1_000_001)
            if not line or len(line) > 1_000_000:
                raise RuntimeError(
                    "CJDB bridge disconnected or exceeded protocol limit: " + self._logs.text()
                )
            response = json.loads(line)
            if response.get("event") == "inferior_started":
                if response.get("id") not in (self._sequence, self._launch_request):
                    raise RuntimeError("CJDB launch ownership response ID mismatch")
                group = response.get("process_group")
                if (
                    type(group) is not int
                    or group <= 1
                    or group != response.get("pid")
                    or group in (os.getpgrp(), self._process.pid)
                ):
                    raise RuntimeError("CJDB reported invalid inferior process-group ownership")
                self._inferior_group = group
                if response.get("id") != self._sequence:
                    raise RuntimeError("CJDB launch was interrupted; inferior ownership retained")
                self._socket.sendall(
                    (json.dumps({"id": self._sequence, "event": "inferior_owned"}) + "\n").encode()
                )
                continue
            if response.get("id") != self._sequence:
                raise RuntimeError("CJDB protocol response ID mismatch")
            self.context = response
            self._inferior_group = response.get("process_group")
            return response

    def _observation(self, status: Status, output: str) -> Observation:
        self._sequence += 1
        return Observation(
            f"obs-{self._sequence}",
            status,
            self.state,
            output,
            self.context.get("frames", []),
            self.context.get("breakpoints", []),
            self.context.get("mode") == "stopped",
        )

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "frames": self.context.get("frames", []),
            "breakpoints": self.context.get("breakpoints", []),
            "capabilities": dict(self.capabilities),
        }

    def execute(self, action: dict) -> Observation:
        try:
            return self._execute(action)
        except (ValueError, TypeError, KeyError) as error:
            return self._observation("invalid", str(error))
        except (OSError, RuntimeError) as error:
            self.close()
            self.state = "PostMortem"
            self.context = {"frames": [], "breakpoints": [], "mode": "unavailable"}
            return self._observation(
                "error", f"Debugger unavailable: {error}. Create a new session."
            )

    def _execute(self, action: dict) -> Observation:
        if not isinstance(action, dict):
            raise ValueError("action must be a JSON object")
        if self._closed:
            raise ValueError("session is closed")
        kind = action.get("action")
        if kind == "set_breakpoint":
            if self.state not in ("Start", "RuntimeState", "RuntimeError"):
                raise ValueError(f"set_breakpoint unavailable at {self.state}; restart first")
            file, line = action.get("file", self.project.editable), action.get("line")
            if file not in self.project.sources or type(line) is not int or line <= 0:
                raise ValueError("breakpoint needs a configured source and positive integer line")
            lines = (self.project.root / file).read_text().splitlines()
            if (
                line > len(lines)
                or not lines[line - 1].strip()
                or lines[line - 1]
                .lstrip()
                .startswith(("//", "/*", "*", "}", "import ", "package "))
            ):
                raise ValueError("breakpoint line is blank, a comment or not a statement")
            response = self._request(
                "breakpoint", file=str((self.project.root / file).resolve()), line=line
            )
        elif kind == "control_execution":
            command = action.get("command")
            if command == "restart":
                response = self._request("restart")
                self.state = "Start"
                self.capabilities.update(mutation="unknown", expression="unknown")
            elif command == "continue":
                if self.state in ("Done", "PostMortem"):
                    raise ValueError(f"continue unavailable at {self.state}; restart first")
                env = self.tools.environment()
                env.update(getattr(self, "launch", {}).get("environment", {}))
                response = self._request(
                    "continue",
                    args=self.case.args,
                    cwd=getattr(self, "launch", {}).get("cwd", str(self.project.root)),
                    stdin_file=str(self.stdin_file),
                    timeout=self.timeout,
                    environment=[f"{k}={v}" for k, v in env.items()],
                )
                mode = response["mode"]
                if mode == "exited":
                    self.state = "Done" if response["exit_code"] == 0 else "PostMortem"
                elif mode == "stopped":
                    self.state = (
                        "RuntimeState" if response["reason"] == "breakpoint" else "PostMortem"
                    )
                else:
                    self.state = "RuntimeError"
            else:
                raise ValueError("control_execution command must be continue or restart")
        elif kind in ("inspect", "interact_code"):
            if self.state == "Start" or self.context.get("mode") != "stopped":
                raise ValueError("navigate to a live stopped frame with a breakpoint first")
            if kind == "inspect":
                frame = action.get("frame", 0)
                variable = action.get("variable")
                if type(frame) is not int or frame < 0:
                    raise ValueError("frame must be a nonnegative integer")
                if variable is not None and (
                    not isinstance(variable, str)
                    or not re.fullmatch(r"[A-Za-z_]\w*(?:\.\w+|\[\d+\])*", variable)
                ):
                    raise ValueError("variable must be a simple variable/member/index path")
                response = self._request("inspect", frame=frame, variable=variable)
            else:
                code = action.get("code")
                if (
                    not isinstance(code, str)
                    or len(code) > 4096
                    or any(ord(c) < 32 or c == ";" for c in code)
                ):
                    raise ValueError("interact_code requires one native command without separators")
                if not re.match(r"^(print\s+\S|set\s+[A-Za-z_]\w*\s*=\s*\S|expression\s+\S)", code):
                    raise ValueError("interact_code supports native print, set or expression only")
                response = self._request("interact", code=code)
                capability = "mutation" if code.startswith("set ") else "expression"
                if code.startswith(("set ", "expression ")):
                    self.capabilities[capability] = "supported" if response["ok"] else "unknown"
                # This CJDB also wraps parse and execution failures in
                # "unsupported expression"; that wrapper proves no capability limit.
                unsupported = any(
                    message in response["output"].lower()
                    for message in (
                        "no jit compiled function",
                        "not supported",
                    )
                )
                if not response["ok"] and unsupported:
                    self.capabilities[capability] = "unsupported"
                    return self._observation("unsupported", response["output"])
            if self.state != "PostMortem":
                self.state = "RuntimeState" if response["ok"] else "RuntimeError"
        else:
            raise ValueError(
                "unknown action; use set_breakpoint, control_execution, inspect, interact_code"
            )
        status: Status = "ok" if response["ok"] else "error"
        if response.get("timed_out"):
            status = "timeout"
        return self._observation(status, response["output"])

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            inferior_group = self._inferior_group
            try:
                self._socket.settimeout(0.5)
                self._request("close")
            except (OSError, RuntimeError, ValueError):
                pass
            finally:
                # The bridge may be hung or dead; it announced ownership before waiting.
                inferior_group = self._inferior_group or inferior_group
                if inferior_group is not None:
                    try:
                        os.killpg(inferior_group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self._inferior_group = None
                kill_group(self._process)
                self._stream.close()
                self._socket.close()
                if self._process.stdout:
                    self._process.stdout.close()
                self._reader.join(timeout=1)
        self._temporary.cleanup()

    def __enter__(self) -> CJDBSession:
        return self

    def __exit__(self, *_args) -> None:
        self.close()
