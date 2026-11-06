"""Executable SB-boundary tests; real CJDB coverage lives in the native suite."""

import importlib
import json
import struct
import sys
import uuid
from types import SimpleNamespace

import pytest

INVALID = (1 << 64) - 1
IDENTITY = "A863545C-45F4-3E16-8857-C815702C4E29"
PATH = "/sdk/libcangjie-runtime.dylib"
BASE = 0x240000000
TRIPLE = "arm64-apple-macosx12.0.0"


class Error:
    def __init__(self, message=""):
        self.message = message

    def Fail(self):
        return bool(self.message)

    def __str__(self):
        return self.message


class Address:
    def __init__(self, section, offset=0):
        self.section, self.offset = section, offset

    def GetFileAddress(self):
        return self.section.file + self.offset

    def GetLoadAddress(self, target):
        load = self.section.GetLoadAddress(target)
        return INVALID if load == INVALID else load + self.offset

    def IsValid(self):
        return True


class Section:
    def __init__(self, name, file, size, load=INVALID):
        self.name, self.file, self.size, self.load = name, file, size, load

    def GetName(self):
        return self.name

    def GetFileAddress(self):
        return self.file

    def GetByteSize(self):
        return self.size

    def GetLoadAddress(self, target):
        return self.load


class Module:
    def __init__(self, file_base=0x100000):
        self.path, self.identity, self.triple = PATH, IDENTITY, TRIPLE
        self.sections = [
            Section("__PAGEZERO", 0, file_base),
            Section("__TEXT", file_base, 0x1000),
            Section("__DATA", file_base + 0x1000, 0x1000),
        ]
        self.symbols = {"CJ_MCC_HandleSafepoint": 0x100, "CJ_MCC_GetExceptionWrapper": 0x200}

    def IsValid(self):
        return True

    def GetFileSpec(self):
        return SimpleNamespace(fullpath=self.path, GetFilename=lambda: self.path.split("/")[-1])

    def GetUUIDString(self):
        return self.identity

    def GetTriple(self):
        return self.triple

    def GetObjectFileHeaderAddress(self):
        return Address(self.sections[1])

    def GetNumSections(self):
        return len(self.sections)

    def GetSectionAtIndex(self, index):
        return self.sections[index]

    def FindSymbol(self, name, kind):
        return SimpleNamespace(
            IsValid=lambda: name in self.symbols,
            GetStartAddress=lambda: Address(self.sections[1], self.symbols[name]),
        )


class Target:
    def __init__(self, module):
        self.module, self.modules, self.writes, self.clears = module, [], [], []
        self.triple = TRIPLE
        self.write_error = False
        self.bad_symbol_load = False

    def GetTriple(self):
        return self.triple

    def GetNumModules(self):
        return len(self.modules)

    def GetModuleAtIndex(self, index):
        return self.modules[index]

    def AddModule(self, module):
        self.modules.append(module)
        return True

    def RemoveModule(self, module):
        self.modules.remove(module)
        return True

    def SetSectionLoadAddress(self, section, load):
        self.writes.append((section.name, load))
        if self.write_error:
            return Error("section refused")
        section.load = load + (4 if self.bad_symbol_load else 0)
        return Error()

    def ClearSectionLoadAddress(self, section):
        self.clears.append(section.name)
        section.load = INVALID
        return Error()


class Process:
    def __init__(self):
        commands = struct.pack("<II", 0x1B, 24) + uuid.UUID(IDENTITY).bytes
        self.header = struct.pack("<8I", 0xFEEDFACF, 0x100000C, 0, 6, 1, len(commands), 0, 0)
        self.memory = self.header + commands
        self.reads = []

    def ReadMemory(self, address, size, error):
        self.reads.append((address, size))
        if BASE <= address < BASE + len(self.memory):
            return self.memory[address - BASE : address - BASE + size]
        if address in (BASE + 0x100, BASE + 0x200):
            return b"\x01" * size
        error.message = "unmapped memory"
        return b""


class Result:
    def Succeeded(self):
        return self.ok

    def GetOutputSize(self):
        return len(self.output)

    def GetErrorSize(self):
        return len(self.error)

    def GetOutput(self):
        return self.output

    def GetError(self):
        return self.error


class Debugger:
    def __init__(self):
        image = {
            "pathname": PATH,
            "uuid": IDENTITY,
            "load_address": BASE,
            "mach_header": {"cputype": 0x100000C, "cpusubtype": 0, "filetype": 6},
        }
        self.reply = (
            '  packet: jGetLoadedDynamicLibrariesInfos:{"fetch_all_solibs":true}\nresponse: '
        )
        self.reply += json.dumps({"images": [image]}) + "\n"
        self.ok, self.error, self.timeout, self.calls = True, "", "17", []

    def GetInstanceName(self):
        return "test"

    def GetInternalVariableValue(self, name, instance):
        return SimpleNamespace(GetStringAtIndex=lambda index: self.timeout)

    def SetInternalVariable(self, name, value, instance):
        self.timeout = value
        return Error()

    def GetCommandInterpreter(self):
        return self

    def HandleCommand(self, command, result):
        self.calls.append(command)
        result.ok, result.output, result.error = self.ok, self.reply, self.error


@pytest.fixture
def runtime(monkeypatch):
    module, debugger, process = Module(), Debugger(), Process()
    target = Target(module)
    api = SimpleNamespace(
        LLDB_INVALID_ADDRESS=INVALID,
        eSymbolTypeCode=2,
        SBError=Error,
        SBCommandReturnObject=Result,
        SBFileSpec=lambda path: path,
        SBModuleSpec=lambda: SimpleNamespace(SetFileSpec=lambda x: None, SetTriple=lambda x: None),
        SBModule=lambda spec: module,
    )
    monkeypatch.setitem(sys.modules, "lldb", api)
    sys.modules.pop("inspect_cj.cjdb_runtime", None)
    helper = importlib.import_module("inspect_cj.cjdb_runtime")
    sync = helper.RuntimeModules(debugger, target, process)
    return SimpleNamespace(
        sync=sync,
        helper=helper,
        module=module,
        debugger=debugger,
        target=target,
        process=process,
        api=api,
    )


def test_missing_mapping_uses_header_slide_not_base_as_slide(runtime):
    runtime.sync.ensure()
    assert runtime.target.writes == [("__TEXT", BASE), ("__DATA", BASE + 0x1000)]
    assert runtime.debugger.calls == [
        "process plugin packet send 'jGetLoadedDynamicLibrariesInfos:{\"fetch_all_solibs\":true}'"
    ]
    assert runtime.debugger.timeout == "17"
    runtime.sync.ensure()
    assert len(runtime.debugger.calls) == 1
    assert len(runtime.target.writes) == 2


def test_healthy_mapping_does_not_query_or_overwrite(runtime):
    runtime.target.modules = [runtime.module]
    runtime.module.sections[1].load = BASE
    runtime.module.sections[2].load = BASE + 0x1000
    runtime.sync.ensure()
    assert runtime.debugger.calls == runtime.target.writes == []


def test_partial_mapping_only_fills_missing_section(runtime):
    runtime.target.modules = [runtime.module]
    runtime.module.sections[2].load = BASE + 0x1000
    runtime.sync.ensure()
    assert runtime.target.writes == [("__TEXT", BASE)]
    runtime.sync.reset()
    assert runtime.module.sections[2].load == BASE + 0x1000
    assert runtime.module.sections[1].load == INVALID


def test_conflicting_mapping_is_never_overwritten(runtime):
    runtime.target.modules = [runtime.module]
    runtime.module.sections[2].load = BASE + 0x2000
    with pytest.raises(runtime.helper.RuntimeSyncError, match="conflict"):
        runtime.sync.ensure()
    assert runtime.target.writes == []
    assert runtime.module.sections[2].load == BASE + 0x2000


@pytest.mark.parametrize(
    "failure",
    [
        "malformed",
        "oversized",
        "too-many-images",
        "failed",
        "missing",
        "duplicate",
        "uuid",
        "architecture",
        "base",
        "path",
        "memory-identity",
        "memory-commands",
        "missing-symbol",
        "write",
        "symbol-address",
    ],
)
def test_discovery_failures_are_bounded_cached_and_do_not_leave_mappings(runtime, failure):
    image = json.loads(runtime.debugger.reply.split("response: ")[1])["images"][0]
    if failure == "malformed":
        runtime.debugger.reply = "response: {"
    elif failure == "oversized":
        runtime.debugger.reply = "response: " + " " * 600_000
    elif failure == "too-many-images":
        runtime.debugger.reply = "response: " + json.dumps({"images": [{}] * 513})
    elif failure == "failed":
        runtime.debugger.ok, runtime.debugger.error = False, "packet failed"
    elif failure == "missing":
        runtime.debugger.reply = 'response: {"images": []}'
    elif failure == "duplicate":
        runtime.debugger.reply = "response: " + json.dumps({"images": [image, image]})
    elif failure == "uuid":
        runtime.module.identity = "00000000-0000-0000-0000-000000000000"
    elif failure == "architecture":
        runtime.module.triple = "x86_64-apple-macosx12.0.0"
    elif failure in ("base", "path"):
        image["load_address" if failure == "base" else "pathname"] = (
            True if failure == "base" else "relative/libcangjie-runtime.dylib"
        )
        runtime.debugger.reply = "response: " + json.dumps({"images": [image]})
    elif failure == "memory-identity":
        runtime.process.memory = runtime.process.header + struct.pack("<II", 0x1B, 24) + b"\0" * 16
    elif failure == "memory-commands":
        runtime.process.memory = struct.pack("<8I", 0xFEEDFACF, 0x100000C, 0, 6, 1, 100_000, 0, 0)
    elif failure == "missing-symbol":
        runtime.module.symbols.clear()
    elif failure == "write":
        runtime.target.write_error = True
    elif failure == "symbol-address":
        runtime.target.bad_symbol_load = True
    with pytest.raises(runtime.helper.RuntimeSyncError) as first:
        runtime.sync.ensure()
    assert len(str(first.value)) < 1000
    calls = len(runtime.debugger.calls)
    with pytest.raises(runtime.helper.RuntimeSyncError):
        runtime.sync.ensure()
    assert len(runtime.debugger.calls) == calls
    assert all(section.load == INVALID for section in runtime.module.sections)
    assert runtime.debugger.timeout == "17"


def test_linux_does_not_use_darwin_packet(runtime):
    runtime.target.triple = "aarch64-unknown-linux-gnu"
    runtime.sync.ensure()
    assert runtime.debugger.calls == runtime.target.writes == []


def test_reset_discards_failure_and_only_removes_still_owned_addresses(runtime):
    runtime.debugger.ok = False
    with pytest.raises(runtime.helper.RuntimeSyncError):
        runtime.sync.ensure()
    runtime.sync.reset()
    runtime.debugger.ok = True
    runtime.sync.ensure()
    runtime.module.sections[2].load = BASE + 0x9000
    runtime.sync.reset()
    assert runtime.module.sections[1].load == INVALID
    assert runtime.module.sections[2].load == BASE + 0x9000


def test_bridge_never_replays_a_failing_state_changing_expression(runtime, monkeypatch):
    monkeypatch.setitem(sys.modules, "cjdb_runtime", runtime.helper)
    sys.modules.pop("inspect_cj.cjdb_bridge", None)
    bridge = importlib.import_module("inspect_cj.cjdb_bridge")
    session = bridge.NativeSession.__new__(bridge.NativeSession)
    session.runtime_modules = runtime.sync
    session.snapshot = lambda: {"mode": "stopped"}
    executed = []

    def expression(code):
        executed.append(code)
        return False, "native failure after execution"

    session.command = expression
    result = session.dispatch({"operation": "interact", "code": "expression bump()"}, None)
    assert not result["ok"]
    assert executed == ["expression bump()"]


def test_bridge_discovery_failure_prevents_expression_execution(runtime, monkeypatch):
    monkeypatch.setitem(sys.modules, "cjdb_runtime", runtime.helper)
    sys.modules.pop("inspect_cj.cjdb_bridge", None)
    bridge = importlib.import_module("inspect_cj.cjdb_bridge")
    session = bridge.NativeSession.__new__(bridge.NativeSession)
    session.runtime_modules = runtime.sync
    session.command = lambda code: pytest.fail("expression executed after failed discovery")
    runtime.debugger.ok = False
    with pytest.raises(runtime.helper.RuntimeSyncError):
        session.dispatch({"operation": "interact", "code": "expression bump()"}, None)


def test_discovery_time_budget_is_enforced_and_timeout_restored(runtime, monkeypatch):
    times = iter([0, 5])
    monkeypatch.setattr(runtime.helper.time, "monotonic", lambda: next(times))
    with pytest.raises(runtime.helper.RuntimeSyncError, match="time budget"):
        runtime.sync.ensure()
    assert runtime.debugger.timeout == "17"
    assert runtime.target.writes == []


def test_loaded_text_still_fills_missing_data(runtime):
    runtime.target.modules = [runtime.module]
    runtime.module.sections[1].load = BASE
    runtime.sync.ensure()
    assert runtime.target.writes == [("__DATA", BASE + 0x1000)]


def test_loaded_symbols_do_not_hide_conflicting_data_mapping(runtime):
    runtime.target.modules = [runtime.module]
    runtime.module.sections[1].load = BASE
    runtime.module.sections[2].load = BASE + 0x3000
    with pytest.raises(runtime.helper.RuntimeSyncError, match="conflict"):
        runtime.sync.ensure()
    assert runtime.target.writes == []


def test_later_conflict_does_not_clear_prior_correct_registrations(runtime):
    runtime.sync.ensure()
    runtime.module.sections[2].load = BASE + 0x3000
    with pytest.raises(runtime.helper.RuntimeSyncError, match="conflict"):
        runtime.sync.ensure()
    assert runtime.module.sections[1].load == BASE
    assert runtime.module.sections[2].load == BASE + 0x3000


@pytest.mark.parametrize(
    "failure",
    [
        "module-count",
        "section-count",
        "packet-cpu",
        "packet-uuid",
        "negative-address",
        "float-address",
        "overflow-address",
        "invalid-symbol-address",
        "short-memory",
    ],
)
def test_untrusted_counts_identity_and_address_ranges_fail_before_execution(runtime, failure):
    image = json.loads(runtime.debugger.reply.split("response: ")[1])["images"][0]
    if failure == "module-count":
        runtime.target.GetNumModules = lambda: 513
    elif failure == "section-count":
        runtime.module.GetNumSections = lambda: 65
    elif failure == "packet-cpu":
        image["mach_header"]["cputype"] = 0x1000007
    elif failure == "packet-uuid":
        image["uuid"] = "00000000-0000-0000-0000-000000000000"
    elif failure.endswith("address"):
        if failure == "invalid-symbol-address":
            runtime.module.symbols["CJ_MCC_HandleSafepoint"] = INVALID
        else:
            image["load_address"] = {
                "negative-address": -1,
                "float-address": float(BASE),
                "overflow-address": INVALID,
            }[failure]
    elif failure == "short-memory":
        runtime.process.memory = b"x"
    runtime.debugger.reply = "response: " + json.dumps({"images": [image]})
    with pytest.raises(runtime.helper.RuntimeSyncError):
        runtime.sync.ensure()
    assert all(section.load == INVALID for section in runtime.module.sections)
    assert len(runtime.debugger.calls) <= 1
    assert len(runtime.target.writes) <= 2


def test_restart_cleans_up_inferior_even_if_metadata_reset_fails(runtime, monkeypatch):
    monkeypatch.setitem(sys.modules, "cjdb_runtime", runtime.helper)
    sys.modules.pop("inspect_cj.cjdb_bridge", None)
    bridge = importlib.import_module("inspect_cj.cjdb_bridge")
    session = bridge.NativeSession.__new__(bridge.NativeSession)
    runtime.sync.ensure()
    runtime.target.ClearSectionLoadAddress = lambda section: Error("clear refused")
    session.runtime_modules, session.target, session.process = (
        runtime.sync,
        runtime.target,
        runtime.process,
    )
    events = []
    session.terminate_inferior = lambda: events.append("terminated")
    runtime.target.DeleteAllBreakpoints = lambda: events.append("breakpoints cleared")
    with pytest.raises(runtime.helper.RuntimeSyncError, match="clear"):
        session.dispatch({"operation": "restart"}, None)
    assert events == ["terminated", "breakpoints cleared"]
    assert session.process is session.runtime_modules is None


def test_snapshot_consumes_hit_one_shots_during_async_native_notification(runtime, monkeypatch):
    monkeypatch.setitem(sys.modules, "cjdb_runtime", runtime.helper)
    sys.modules.pop("inspect_cj.cjdb_bridge", None)
    bridge = importlib.import_module("inspect_cj.cjdb_bridge")
    session = bridge.NativeSession.__new__(bridge.NativeSession)
    session.process, session.process_group = None, None
    deleted = []
    breakpoints = [
        SimpleNamespace(
            GetID=lambda: 1,
            IsEnabled=lambda: True,
            IsOneShot=lambda: True,
            GetHitCount=lambda: 1,
            GetNumLocations=lambda: 1,
        ),
        SimpleNamespace(
            GetID=lambda: 2,
            IsEnabled=lambda: True,
            IsOneShot=lambda: False,
            GetHitCount=lambda: 1,
            GetNumLocations=lambda: 1,
        ),
    ]
    session.target = SimpleNamespace(
        GetNumBreakpoints=lambda: len(breakpoints),
        GetBreakpointAtIndex=lambda index: breakpoints[index],
        BreakpointDelete=lambda id: deleted.append(id),
    )
    snapshot = session.snapshot()
    assert [bp["id"] for bp in snapshot["breakpoints"]] == [2]
    assert deleted == [1]


def test_repeated_metadata_loss_has_bounded_owned_registrations(runtime):
    for _ in range(70):
        for section in runtime.module.sections:
            section.load = INVALID
        runtime.sync.ensure()
    loads = []
    for section in runtime.module.sections:
        original = section.GetLoadAddress

        def counted(target, original=original):
            loads.append(1)
            return original(target)

        section.GetLoadAddress = counted
    runtime.sync.reset()
    assert len(loads) <= 64
    assert len(runtime.target.clears) == 2
    assert len(runtime.target.writes) == 140


@pytest.mark.parametrize("healthy", [False, True])
def test_delayed_final_symbol_read_rejects_expression_and_caches_failure(
    runtime, monkeypatch, healthy
):
    if healthy:
        runtime.sync.ensure()
    clock = [0.0]
    monkeypatch.setattr(runtime.helper.time, "monotonic", lambda: clock[0])
    original = runtime.process.ReadMemory

    def delayed(address, size, error):
        result = original(address, size, error)
        if address == BASE + 0x200:
            clock[0] = 5.0
        return result

    runtime.process.ReadMemory = delayed
    monkeypatch.setitem(sys.modules, "cjdb_runtime", runtime.helper)
    sys.modules.pop("inspect_cj.cjdb_bridge", None)
    bridge = importlib.import_module("inspect_cj.cjdb_bridge")
    session = bridge.NativeSession.__new__(bridge.NativeSession)
    session.runtime_modules = runtime.sync
    session.command = lambda code: pytest.fail("expression ran after final read exceeded deadline")
    with pytest.raises(runtime.helper.RuntimeSyncError, match="time budget"):
        session.dispatch({"operation": "interact", "code": "expression bump()"}, None)
    reads = len(runtime.process.reads)
    clock[0] = 6.0
    with pytest.raises(runtime.helper.RuntimeSyncError, match="time budget"):
        runtime.sync.ensure()
    assert len(runtime.process.reads) == reads
    if healthy:
        assert runtime.module.sections[1].load == BASE
        assert runtime.module.sections[2].load == BASE + 0x1000


@pytest.mark.parametrize("healthy", [False, True])
def test_deadline_crossed_after_final_read_blocks_successful_handoff(runtime, monkeypatch, healthy):
    if healthy:
        runtime.sync.ensure()
    final_read = [False]
    checks_after_read = [0]
    original = runtime.process.ReadMemory

    def clock():
        if final_read[0]:
            checks_after_read[0] += 1
            # The read itself finishes in budget; the handoff crosses the deadline.
            return 0.0 if checks_after_read[0] == 1 else 5.0
        return 0.0

    def read(address, size, error):
        result = original(address, size, error)
        if address == BASE + 0x200:
            final_read[0] = True
        return result

    runtime.process.ReadMemory = read
    monkeypatch.setattr(runtime.helper.time, "monotonic", clock)
    with pytest.raises(runtime.helper.RuntimeSyncError, match="time budget"):
        runtime.sync.ensure()
