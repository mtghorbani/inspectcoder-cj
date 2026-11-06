# Native cjpm tests

The native adapter is verified with Cangjie SDK/cjpm 1.1.0 on macOS arm64 and the matching SDK CJDB.
Other SDK versions and platforms return unsupported rather than inheriting this certification.
The standalone controller remains available on its independently supported toolchains.

## 1. Install and check the matching tools

Follow [Getting started](../GETTING_STARTED.md) to install the Python engine and matching Cangjie SDK without replacing system defaults.
Persist your own SDK selection and, when needed, a compatible Apple sysroot:

```sh
inspectcoder-cj configure --sdk "$HOME/Tools/cangjie"
inspectcoder-cj configure --sysroot /path/to/compatible/MacOSX15.4.sdk
inspectcoder-cj doctor
```

The sysroot path is an example that must be replaced with your Apple SDK installation.
Environment overrides such as `CJC`, `CJDB`, `CJPM` and `INSPECT_CJ_SYSROOT` remain optional automation inputs.
InspectCJ derives or loads the selected SDK root and sets `CANGJIE_HOME` only in tool child environments.
It prepends that SDK's `bin`, `tools/bin`, and `third_party/llvm/bin` directories to the child PATH so compiler helpers and linkers use the selected SDK.
The macOS debugger receives the installed Python 3.11 framework path only in its child environment.
InspectCJ also generates a private compiler shim on the cjpm child's PATH to supply the explicit sysroot to every cjc invocation.
This includes cjpm's own std.testrunner, which does not receive package compile-option settings in the verified SDK.
The shim delegates to the selected real compiler and does not replace SDK binaries, alter the parent PATH, or change Xcode/security settings.

## 2. Create a package with real native assertions

```sh
/absolute/path/to/cangjie/tools/bin/cjpm init --name bounds --type=static --path bounds
mkdir -p bounds/src
```

Replace the executable path with `tools/bin/cjpm` from your selected SDK.
Saving the SDK configuration does not add its commands to your terminal's PATH.

Create `bounds/src/lib.cj`:

```cangjie
package bounds

public func boundedIndex(index: Int64, size: Int64): Int64 {
    if (index < 0) { return 0 }
    if (index >= size) { return size }
    return index
}
```

Create `bounds/src/lib_test.cj`:

```cangjie
package bounds
import std.unittest.*
import std.unittest.testmacro.*

@Test
public class Bounds {
    @TestCase
    func high() { @Expect(boundedIndex(5, 3), 2) }
    @TestCase
    func low() { @Expect(boundedIndex(-1, 3), 0) }
}
```

These independently supplied native assertions provide correctness without task prose.
The high case fails with actual 3 and expected 2, while the low case passes.

## 3. Reproduce and repair

```sh
inspect-cj test bounds
inspect-cj repair bounds --transport codex --non-interactive
```

Test returns JSON with each native result and a stable opaque `cjpm:...` case identity.
An assertion failure returns exit 1.
Repair chooses the first failing identity in a stable order, builds with debug symbols through cjpm, and inspects that exact native case under persistent CJDB.
Native assertions and test lifecycle functions remain active during the focused debug launch.
The final candidate is accepted only after an independently executed full native suite, including previously passing cases.
Neither native case discovery nor --no-run compilation is treated as integration or acceptance evidence.

For explicit selection, copy the opaque identity from the test result:

```sh
inspect-cj repair bounds --case 'cjpm:COPY_THE_DISCOVERED_ID' \
  --editable src/lib.cj --task 'Preserve public signatures' --non-interactive
```

`--editable` names exactly one implementation file relative to the discovered package/workspace root.
It is optional only when that scope is unambiguous.
Files containing native tests or lifecycle macros cannot be editable implementations.
A source-file target inside a native project also selects that implementation explicitly:

```sh
inspect-cj repair bounds/src/lib.cj --non-interactive
```

Review and use the same [guarded apply command](standalone.md#artifacts-replay-and-application) as for standalone repair.
A selected case that already passes while other baseline cases fail returns needs_input with remaining failure choices.
Candidate validation always retains the complete suite even when a particular case was selected for inspection.

## Packages, dependencies, resources and workspaces

The adapter uses actual cjpm package boundaries and native runner launch metadata.
It does not flatten multiple packages into direct cjc source arguments.
Multiple packages in a module, relative local dependencies inside the selected root, and declared workspace member suites are tested.
A containing declared workspace controls its real member build/test semantics.
Dependency-module tests remain governed by cjpm's declared scope; they are not silently added to the selected module suite.

Sources, tests, manifests, existing locks, resources and executable resource modes are copied to a private snapshot.
Relative resource paths and native fixture lifecycle execute from that snapshot's cjpm root.
The original tree is not used for builds, generated locks, native reports or debugger mutation.
The initial snapshot fixes the acceptance bytes and file membership for the entire repair.
Before validation and inspection, after debugger restarts, and when inspection ends, the controller restores that owned snapshot while retaining the current editable implementation.
This removes fixture-created files, including files absent from the original snapshot, and restores overwritten or deleted inputs after interrupted inspection.
Native fixtures still run normally; recorded acceptance inputs must match their initial bytes after suite execution.

Local dependency/resource paths escaping the selected root, symlinked trees, snapshots larger than 10000 files or 128MiB, more than 1000 cases, and native case names that cannot be filtered exactly are explicitly unsupported.
Use a containing workspace with relative paths when local modules need a shared root.
Manifest workspace members, build/test members, source/output directories and dependency/resource paths must be relative, even when an absolute path points inside the selected root.
Discovery, replay and apply reject these absolute paths before builds or source snapshots.
Parameterized/native frameworks with unverified case-selection semantics are not claimed as supported.
Tests reported as skipped make coverage partial and prevent a full-success repair claim.
Build/dependency/environment faults and evaluator errors remain distinguishable from passing native assertions.

## Evidence, replay and cancellation

Each validation stores actual cjpm build/discovery/suite logs, native XML, matching tool versions, executable hashes, full-suite case identities and exact focused launch arguments/cwd/environment.
Discovery membership is compared with executed XML, and candidate membership is compared with the baseline whenever the baseline compiled.
A compile-first repair establishes executable membership after the first successful build while preserving immutable test/resource bytes throughout.
The invocation receipt also records source/input/toolchain hashes, platform and engine source identity.
Replay revalidates original inputs and the matching toolchain, then runs fresh validation.

All nested build/discovery/test/debug/model work shares the repair's absolute `--run-timeout` and attempt/step allowances.
Ctrl-C cancels the CLI, writes an interrupted result, and cleans owned native runner/package/debugger processes.
Native timeout and cancellation tests check for surviving processes rather than inferring cleanup from an exit code.
Manual native debugging uses the same case identities:

```sh
inspect-cj debug bounds --case 'cjpm:COPY_THE_DISCOVERED_ID' --actions actions.jsonl
```

Use the [debugger action reference](usage.md) to supply source breakpoints and inspection actions.
The known SDK array-expression allocator ABI fault remains an evaluator failure; direct inspection and compiled controls are used without patching the SDK or synthesizing results.

## Authoritative behavior references

The installed `cjpm test --help`, compiled native runner `--help`, verbose API-v2 launch envelope and actual XML reports are the authority for this adapter version.
The [official package-manager manual](https://docs.cangjie-lang.cn/en/docs/1.0.0/tools/tools/cjpm_manual_cjnative_community.html) explains module/package test scope and workspace metadata.
The [official native test lifecycle reference](https://docs.cangjie-lang.cn/docs/1.0.0/libs/std/unittest/unittest_samples/unittest_basics.html) describes fixtures retained by native execution.

Native debug opens CJDB only after discovery and suite execution provide a real case launch contract.
A discovery or suite phase timeout emits one JSON result with status `timeout`, exit 4 and the incomplete test results; it does not open a debugger or claim acceptance.
A missing or malformed native launch contract emits status `incomplete`, exit 3.
An ordinary native assertion failure with a valid launch contract remains inspectable as the exact selected case.
Once a debugger session opens, the existing per-action JSONL statuses and exit semantics apply.
Discovery rejects symlinked or nonordinary `cjpm.toml` entries at the selected target and in containing directories before resolving their lexical paths or reading their contents.
Nested manifests and other snapshot entries are checked for symlinks, ordinary file/directory type and canonical containment before nested manifest content is read, including during replay and apply.
