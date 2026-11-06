"""Session-only repair of missing Darwin Cangjie runtime load metadata.

Imported inside CJDB, using its own SB interfaces. No expressions are executed
here: synchronizing metadata must never replay a user's side effects.
"""

import json
import os
import struct
import time
import uuid

import lldb

PACKET = "process plugin packet send 'jGetLoadedDynamicLibrariesInfos:{\"fetch_all_solibs\":true}'"
MAX_PACKET = 512_000
MAX_IMAGES = 512
MAX_SECTIONS = 64
SYMBOLS = ("CJ_MCC_HandleSafepoint", "CJ_MCC_GetExceptionWrapper")
INVALID = lldb.LLDB_INVALID_ADDRESS


class RuntimeSyncError(ValueError):
    pass


def architecture(triple):
    arch = (triple or "").split("-")[0]
    return "arm64" if arch == "aarch64" else arch


def identity(value):
    if not isinstance(value, str) or len(value) != 36:
        raise RuntimeSyncError("invalid runtime UUID")
    return uuid.UUID(value)


def address(value):
    if type(value) is not int or not 0 < value < INVALID:
        raise RuntimeSyncError("invalid runtime address")
    return value


class RuntimeModules:
    def __init__(self, debugger, target, process):
        self.debugger, self.target, self.process = debugger, target, process
        self.failure = None
        self.owned = []
        self.deadline = 0

    def reset(self):
        self.clear_owned(0)
        self.failure = None

    def clear_owned(self, start):
        # Remove only our unchanged registrations, never a later loader correction.
        for section, load in reversed(self.owned[start:]):
            if section.GetLoadAddress(self.target) == load:
                error = self.target.ClearSectionLoadAddress(section)
                if error.Fail():
                    raise RuntimeSyncError("could not clear owned runtime section: " + str(error))
        del self.owned[start:]

    def check_time(self):
        if time.monotonic() > self.deadline:
            raise RuntimeSyncError("runtime synchronization exceeded its time budget")

    def read(self, load, size):
        self.check_time()
        address(load)
        if not 0 < size <= 65536 or load + size >= INVALID:
            raise RuntimeSyncError("invalid runtime memory range")
        error = lldb.SBError()
        data = self.process.ReadMemory(load, size, error)
        self.check_time()
        if error.Fail() or len(data) != size:
            raise RuntimeSyncError("runtime memory could not be verified")
        return data

    def verify_identity(self, module, load, image=None):
        arch = architecture(self.target.GetTriple())
        if architecture(module.GetTriple()) != arch:
            raise RuntimeSyncError("runtime architecture mismatch")
        magic, cpu, subtype, kind, count, size, _, _ = struct.unpack("<8I", self.read(load, 32))
        if (
            magic != 0xFEEDFACF
            or cpu != {"arm64": 0x100000C, "x86_64": 0x1000007}.get(arch)
            or kind != 6
            or not 0 < count <= 128
            or not 0 < size <= 65536
        ):
            raise RuntimeSyncError("invalid live runtime Mach-O header or architecture")
        if image is not None:
            header = image.get("mach_header")
            if not isinstance(header, dict) or any(
                type(header.get(key)) is not int or header[key] != value
                for key, value in (("cputype", cpu), ("cpusubtype", subtype), ("filetype", kind))
            ):
                raise RuntimeSyncError("loaded-image architecture/header mismatch")
        commands = self.read(load + 32, size)
        offset, identities = 0, []
        for _ in range(count):
            if offset + 8 > size:
                raise RuntimeSyncError("invalid runtime load commands")
            command, length = struct.unpack_from("<II", commands, offset)
            if length < 8 or length % 8 or offset + length > size:
                raise RuntimeSyncError("invalid runtime load command size")
            if command == 0x1B:
                if length != 24:
                    raise RuntimeSyncError("invalid runtime UUID command")
                identities.append(uuid.UUID(bytes=commands[offset + 8 : offset + 24]))
            offset += length
        if offset != size or identities != [identity(module.GetUUIDString())]:
            raise RuntimeSyncError("live runtime identity mismatch")
        if image is not None and identities != [identity(image.get("uuid"))]:
            raise RuntimeSyncError("loaded-image runtime identity mismatch")

    def symbols(self, module, load):
        header = module.GetObjectFileHeaderAddress().GetFileAddress()
        for name in SYMBOLS:
            symbol = module.FindSymbol(name, lldb.eSymbolTypeCode)
            if not symbol.IsValid():
                raise RuntimeSyncError("required runtime symbol missing: " + name)
            start = symbol.GetStartAddress()
            expected = address(load + start.GetFileAddress() - header)
            if start.GetLoadAddress(self.target) != expected:
                raise RuntimeSyncError("required runtime symbol address unavailable: " + name)
            self.read(expected, 1)

    def discover(self):
        name = "plugin.process.gdb-remote.packet-timeout"
        instance = self.debugger.GetInstanceName()
        old = self.debugger.GetInternalVariableValue(name, instance).GetStringAtIndex(0)
        if not old:
            raise RuntimeSyncError("debugger packet timeout unavailable")
        error = self.debugger.SetInternalVariable(name, "2", instance)
        if error.Fail():
            raise RuntimeSyncError("could not bound debugger packet timeout")
        try:
            result = lldb.SBCommandReturnObject()
            self.debugger.GetCommandInterpreter().HandleCommand(PACKET, result)
            self.check_time()
            if not result.Succeeded():
                raise RuntimeSyncError("loaded-image discovery failed")
            if result.GetOutputSize() + result.GetErrorSize() > MAX_PACKET:
                raise RuntimeSyncError("loaded-image discovery exceeded response limit")
            output = result.GetOutput() or ""
            prefix, found, payload = output.partition("response: ")
            if not found or "response:" in prefix:
                raise RuntimeSyncError("loaded-image discovery has no structured response")
            decoded = json.loads(payload)
            images = decoded.get("images") if isinstance(decoded, dict) else None
            if not isinstance(images, list) or not 0 < len(images) <= MAX_IMAGES:
                raise RuntimeSyncError("invalid loaded-image count")
            candidates = []
            for image in images:
                if not isinstance(image, dict):
                    raise RuntimeSyncError("invalid loaded-image entry")
                path = image.get("pathname")
                if not isinstance(path, str) or len(path) > 4096 or any(ord(c) < 32 for c in path):
                    raise RuntimeSyncError("invalid loaded-image path")
                if os.path.basename(path) == "libcangjie-runtime.dylib":
                    if not os.path.isabs(path):
                        raise RuntimeSyncError("runtime image path must be absolute")
                    address(image.get("load_address"))
                    identity(image.get("uuid"))
                    candidates.append(image)
            if len(candidates) != 1:
                raise RuntimeSyncError("expected exactly one loaded Cangjie runtime image")
            return candidates[0]
        finally:
            error = self.debugger.SetInternalVariable(name, old, instance)
            if error.Fail():
                raise RuntimeSyncError("could not restore debugger packet timeout")

    def missing_sections(self, module, load):
        header = module.GetObjectFileHeaderAddress().GetFileAddress()
        if type(header) is not int or not 0 <= header < INVALID:
            raise RuntimeSyncError("runtime object header file address unavailable")
        count = module.GetNumSections()
        if not 0 < count <= MAX_SECTIONS:
            raise RuntimeSyncError("runtime section count exceeded limit")
        pending = []
        for index in range(count):
            section = module.GetSectionAtIndex(index)
            if section.GetName() == "__PAGEZERO" or section.GetByteSize() == 0:
                continue
            file = section.GetFileAddress()
            size = section.GetByteSize()
            if not 0 <= file < INVALID or not 0 < size < INVALID:
                raise RuntimeSyncError("invalid runtime section file range")
            expected = address(file - header + load)
            if expected + size >= INVALID:
                raise RuntimeSyncError("invalid runtime section range")
            current = section.GetLoadAddress(self.target)
            if current not in (INVALID, expected):
                raise RuntimeSyncError("existing runtime section mapping conflicts with live image")
            if current == INVALID:
                pending.append((section, expected))
        return pending

    def ensure(self):
        if "-apple-" not in (self.target.GetTriple() or ""):
            return
        if self.failure:
            raise RuntimeSyncError(self.failure)
        self.owned = [
            (section, load)
            for section, load in self.owned
            if section.GetLoadAddress(self.target) == load
        ]
        self.deadline = time.monotonic() + 4
        added = None
        owned_before = len(self.owned)
        try:
            count = self.target.GetNumModules()
            if count > MAX_IMAGES:
                raise RuntimeSyncError("debugger module count exceeded limit")
            modules = [self.target.GetModuleAtIndex(i) for i in range(count)]
            runtimes = [
                m for m in modules if m.GetFileSpec().GetFilename() == "libcangjie-runtime.dylib"
            ]
            if len(runtimes) > 1:
                raise RuntimeSyncError("ambiguous debugger runtime modules")
            module = runtimes[0] if runtimes else None
            if module:
                load = module.GetObjectFileHeaderAddress().GetLoadAddress(self.target)
                if load != INVALID:
                    self.verify_identity(module, load)
                    if not self.missing_sections(module, load):
                        self.symbols(module, load)
                        self.check_time()
                        return
            image = self.discover()
            if module is None:
                spec = lldb.SBModuleSpec()
                spec.SetFileSpec(lldb.SBFileSpec(image["pathname"]))
                spec.SetTriple(self.target.GetTriple())
                module = lldb.SBModule(spec)
            if not module.IsValid() or os.path.realpath(
                module.GetFileSpec().fullpath
            ) != os.path.realpath(image["pathname"]):
                raise RuntimeSyncError("runtime file does not match the loaded image")
            load = image["load_address"]
            self.verify_identity(module, load, image)
            pending = self.missing_sections(module, load)
            # Validate everything before changing any target metadata.
            if not runtimes:
                if not self.target.AddModule(module):
                    raise RuntimeSyncError("could not add verified runtime module")
                added = module
            for section, expected in pending:
                self.check_time()
                error = self.target.SetSectionLoadAddress(section, expected)
                if error.Fail():
                    raise RuntimeSyncError("could not register runtime section: " + str(error))
                actual = section.GetLoadAddress(self.target)
                self.owned.append((section, actual))
                if actual != expected:
                    raise RuntimeSyncError("runtime section registration address mismatch")
            self.symbols(module, load)
            self.check_time()
        except Exception as error:
            cleanup = ""
            try:
                self.clear_owned(owned_before)
                if added is not None:
                    if not self.target.RemoveModule(added):
                        raise RuntimeSyncError("could not remove added runtime module")
            except Exception as cleanup_error:
                cleanup = "; metadata rollback failed: " + str(cleanup_error)
            self.failure = (
                "Runtime module synchronization failed: "
                + (str(error) + cleanup)[:500]
                + ". Expression was not executed."
            )
            raise RuntimeSyncError(self.failure) from error
