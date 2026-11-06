"""Private worker imported by CJDB, never by the controller's Python runtime.

The installed debugger supplies `lldb`; keeping that import here avoids mixing
Homebrew/system LLDB bindings with Cangjie's compiler-specific language plugin.
"""

import codecs
import json
import os
import signal
import socket
import tempfile
import time

import lldb
from cjdb_runtime import RuntimeModules

MAX_MESSAGE = 1_000_000


def bounded(text, limit=16000):
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n[... omitted ...]\n" + text[-limit // 2 :]


class NativeSession:
    def __init__(self):
        self.debugger = lldb.debugger
        self.debugger.SetAsync(True)
        self.target = None
        self.process = None
        self.runtime_modules = None
        self.process_group = None
        self.output = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.decoder_finished = False
        self.io_directory = tempfile.TemporaryDirectory(prefix="inspect-cj-native-io-")
        self.output_path = os.path.join(self.io_directory.name, "output")
        os.mkfifo(self.output_path, 0o600)
        self.output_fd = os.open(self.output_path, os.O_RDWR | os.O_NONBLOCK)

    def command(self, command):
        response = lldb.SBCommandReturnObject()
        self.debugger.GetCommandInterpreter().HandleCommand(command, response)
        return response.Succeeded(), bounded(
            (response.GetOutput() or "") + (response.GetError() or "")
        )

    def drain(self):
        if not self.process:
            return
        # The FIFO applies backpressure without an unbounded file or LLDB buffer.
        for _ in range(16):
            try:
                chunk = os.read(self.output_fd, 4096)
            except BlockingIOError:
                break
            if not chunk:
                break
            self.output = bounded(self.output + self.decoder.decode(chunk))

    def terminate_inferior(self):
        # Launch establishes a distinct, owned inferior group before execution is awaited.
        # Keep its ID after a normal exit so remaining ordinary children are also reaped.
        group = self.process_group
        self.process_group = None
        if group is not None:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.process and self.process.IsValid():
            self.process.Kill()

    def finish_output(self):
        self.drain()
        if not self.decoder_finished:
            self.output = bounded(self.output + self.decoder.decode(b"", final=True))
            self.decoder_finished = True

    def wait(self, timeout, previous_stop=-1):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.drain()
            state = self.process.GetState()
            if state == lldb.eStateStopped and self.process.GetStopID() == previous_stop:
                time.sleep(0.01)
                continue
            if state in (
                lldb.eStateStopped,
                lldb.eStateCrashed,
                lldb.eStateExited,
                lldb.eStateDetached,
            ):
                if state in (lldb.eStateExited, lldb.eStateDetached):
                    self.terminate_inferior()
                    self.finish_output()
                self.drain()
                return False
            time.sleep(0.01)
        self.terminate_inferior()
        self.finish_output()
        return True

    def snapshot(self):
        frames, breakpoints = [], []
        mode, reason, exit_code = "start", "", None
        pid = None
        if self.target:
            consumed = []
            for index in range(self.target.GetNumBreakpoints()):
                bp = self.target.GetBreakpointAtIndex(index)
                # A stop can become visible before CJDB finishes its async
                # one-shot notification. Retire it once its hit is observed.
                if bp.IsOneShot() and bp.GetHitCount() > 0:
                    consumed.append(bp.GetID())
                    continue
                if bp.IsEnabled():
                    breakpoints.append(
                        {
                            "id": bp.GetID(),
                            "locations": bp.GetNumLocations(),
                            "hits": bp.GetHitCount(),
                            "one_shot": bp.IsOneShot(),
                        }
                    )
            for breakpoint_id in consumed:
                self.target.BreakpointDelete(breakpoint_id)
        if self.process and self.process.IsValid():
            state = self.process.GetState()
            pid = self.process.GetProcessID()
            if state == lldb.eStateExited:
                mode = "exited"
                exit_code = self.process.GetExitStatus()
            elif state in (lldb.eStateStopped, lldb.eStateCrashed):
                mode = "stopped"
                thread = self.process.GetSelectedThread()
                stop = thread.GetStopReason()
                reason = "breakpoint" if stop == lldb.eStopReasonBreakpoint else "error"
                for i in range(min(thread.GetNumFrames(), 32)):
                    frame = thread.GetFrameAtIndex(i)
                    line = frame.GetLineEntry()
                    file = line.GetFileSpec()
                    frames.append(
                        {
                            "index": i,
                            "function": bounded(frame.GetFunctionName() or "?", 500),
                            "file": os.path.join(
                                file.GetDirectory() or "", file.GetFilename() or ""
                            ),
                            "line": line.GetLine(),
                        }
                    )
            else:
                mode = "unavailable"
        return {
            "mode": mode,
            "reason": reason,
            "exit_code": exit_code,
            "frames": frames,
            "breakpoints": breakpoints,
            "pid": pid,
            "process_group": self.process_group,
        }

    def dispatch(self, request, notify):
        operation = request["operation"]
        ok, output, timed_out = True, "", False
        self.output = ""
        if operation == "initialize":
            self.command("settings set use-color false")
            self.command("settings set auto-confirm true")
            self.command("settings set target.load-script-from-symbol-file false")
            self.target = self.debugger.CreateTarget(request["executable"])
            if not self.target.IsValid():
                raise ValueError("CJDB could not load native executable")
            version = lldb.SBDebugger.GetVersionString()
            if "cangjie" not in version.lower():
                raise ValueError("CJDB must identify the Cangjie language plugin")
            output = version
        elif operation == "breakpoint":
            bp = self.target.BreakpointCreateByLocation(
                os.path.realpath(request["file"]), request["line"]
            )
            bp.SetOneShot(True)
            locations = [
                bp.GetLocationAtIndex(i).GetAddress().GetLineEntry().GetLine()
                for i in range(bp.GetNumLocations())
            ]
            if not locations or any(line != request["line"] for line in locations):
                self.target.BreakpointDelete(bp.GetID())
                ok, output = False, "No exact executable location at that source line"
            else:
                output = f"One-shot breakpoint {bp.GetID()} at {request['file']}:{request['line']}"
        elif operation == "continue":
            previous_stop = -1
            if self.process is None:
                # Remove buffered bytes/decoder state belonging to the previous inferior.
                for _ in range(16):
                    try:
                        if not os.read(self.output_fd, 65536):
                            break
                    except BlockingIOError:
                        break
                self.decoder.reset()
                self.decoder_finished = False
                info = lldb.SBLaunchInfo(request["args"])
                info.SetLaunchFlags(lldb.eLaunchFlagLaunchInSeparateProcessGroup)
                info.SetWorkingDirectory(request["cwd"])
                info.AddOpenFileAction(0, request["stdin_file"], True, False)
                info.AddOpenFileAction(1, self.output_path, False, True)
                info.AddOpenFileAction(2, self.output_path, False, True)
                info.SetEnvironmentEntries(request["environment"], True)
                error = lldb.SBError()
                self.process = self.target.Launch(info, error)
                if error.Fail():
                    raise ValueError(str(error))
                pid = self.process.GetProcessID()
                try:
                    group = os.getpgid(pid)
                except ProcessLookupError:
                    # The separate-group launch contract also covers a leader that
                    # already exited while ordinary descendants keep that group alive.
                    group = pid
                if group != pid or group == os.getpgrp():
                    self.process.Kill()
                    raise ValueError("CJDB did not establish an isolated inferior process group")
                self.process_group = group
                # The controller must know ownership even if this request is interrupted.
                notify({"event": "inferior_started", "pid": pid, "process_group": group})
            else:
                previous_stop = self.process.GetStopID()
                error = self.process.Continue()
                if error.Fail():
                    raise ValueError(str(error))
            timed_out = self.wait(request["timeout"], previous_stop)
            output = self.output
            if timed_out:
                ok = False
                output += "\nNative execution timed out; inferior killed. Restart to inspect again."
        elif operation == "restart":
            try:
                if self.runtime_modules is not None:
                    self.runtime_modules.reset()
            finally:
                self.runtime_modules = None
                self.terminate_inferior()
                self.process = None
                self.target.DeleteAllBreakpoints()
            output = "Restarted; runtime mutations and all breakpoints cleared."
        elif operation == "inspect":
            thread = self.process.GetSelectedThread()
            index = request.get("frame", 0)
            if index >= thread.GetNumFrames():
                raise ValueError("frame index outside current stack")
            frame = thread.GetFrameAtIndex(index)
            thread.SetSelectedFrame(index)
            variable = request.get("variable")
            if variable:
                value = frame.GetValueForVariablePath(variable)
                if not value.IsValid() or value.GetError().Fail():
                    ok, output = False, f"Variable {variable!r} is not available in this frame"
                else:
                    output = bounded(str(value))
            else:
                values = frame.GetVariables(True, True, False, True)
                output = bounded(
                    "\n".join(
                        str(values.GetValueAtIndex(i)) for i in range(min(values.GetSize(), 100))
                    )
                )
                if not output:
                    output = "No visible arguments or locals in the selected frame."
        elif operation == "interact":
            if request["code"].split(None, 1)[0] == "expression":
                if self.runtime_modules is None:
                    self.runtime_modules = RuntimeModules(self.debugger, self.target, self.process)
                self.runtime_modules.ensure()
            ok, output = self.command(request["code"])
        elif operation == "close":
            self.terminate_inferior()
        else:
            raise ValueError("unknown bridge operation")
        return {"ok": ok, "output": bounded(output), "timed_out": timed_out, **self.snapshot()}


def serve(fd):
    connection = socket.socket(fileno=fd)
    session = NativeSession()
    with connection, connection.makefile("rwb", buffering=0) as stream:
        try:
            while True:
                line = stream.readline(MAX_MESSAGE + 1)
                if not line:
                    break
                if len(line) > MAX_MESSAGE:
                    break
                request = json.loads(line)

                def send(response, request_id=request["id"]):
                    response["id"] = request_id
                    # Deliver the complete JSON line even when a native stack is large.
                    connection.sendall((json.dumps(response) + "\n").encode())
                    if response.get("event") == "inferior_started":
                        acknowledgement = json.loads(stream.readline(MAX_MESSAGE + 1))
                        if (
                            acknowledgement.get("id") != request_id
                            or acknowledgement.get("event") != "inferior_owned"
                        ):
                            raise ValueError("controller did not acknowledge inferior ownership")

                try:
                    response = session.dispatch(request, send)
                except Exception as error:
                    response = {
                        "ok": False,
                        "output": bounded(str(error)),
                        "timed_out": False,
                        **session.snapshot(),
                    }
                send(response)
                if request["operation"] == "close":
                    break
        finally:
            session.terminate_inferior()
            os.close(session.output_fd)
            session.io_directory.cleanup()
