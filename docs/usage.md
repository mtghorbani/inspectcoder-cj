# Usage and integration

## Project contract

A manifest sits beside the Cangjie source files it names.
Source paths are relative, stay inside that directory and end in `.cj`.
Repair snapshots require ordinary source files rather than symlinks.
Exactly one listed source is editable by PatchCoder.
For function-level repair, keep a separate `main.cj` driver outside `editable`, as in `examples/function`.

```json
{
  "sources": ["bounds.cj", "main.cj"],
  "editable": "bounds.cj",
  "requirement": "Clamp index into 0 through size-1 for positive size.",
  "cjc_flags": [],
  "test_timeout": 10,
  "build_timeout": 60,
  "tests": [
    {"name": "upper", "args": ["5", "3"], "stdin": "", "stdout": "2\n", "exit_code": 0}
  ]
}
```

For a standalone manifest, `requirement`, `sources`, `editable` and a `tests` array are required.
The array must be nonempty unless `compile_only` is explicitly true; see [compilation-only checks](standalone.md#compiler-errors-and-compilation-only-checks) for their distinct outcome.
Each executable case needs a unique name.
Arguments default to `[]`, stdin to `""`, and exit code to `0`.
The default `comparison` is `legacy_combined`, which requires `stdout` and compares it against combined stdout/stderr in capture order.
Select `"comparison": "exact"` to compare streams separately using `stdout` and `stderr`; either may be null or omitted to leave that stream unchecked.
Exact comparison rejects truncated output and always checks `exit_code`.
Tests run independently in manifest order and the first failure becomes the next debugger focus.
Input is delivered from a dedicated file, separate from debugger actions.
There is no interactive terminal input during testing.
Test input is limited to 64KiB, expected output to 60000 bytes, each source to 1MB and suites to 1000 cases.
Model context has a separate 128000-byte bound, so large projects may exhaust context before inference.

Builds use `cjc -g` and the configured source list.
Optional `cjc_flags` are trusted compiler arguments, not shell text.
An optional `sysroot` manifest property names an existing compatible SDK directory.
Save a default with `inspectcoder-cj configure --sysroot PATH`; automatic macOS selection uses xcrun when no override is selected.
The optional `INSPECT_CJ_SYSROOT` automation override remains supported.
Use an absolute path so compiler invocations resolve it consistently.
Use `inspectcoder-cj configure --sdk PATH` for persistent SDK selection without shell exports.
Optional `CJC` and `CJDB` values override executable discovery for automation.
The default CJDB is the SDK's `third_party/llvm/bin/lldb`, not the system LLDB.
The bridge verifies that its version identifies Cangjie support.
Runtime library paths are supplied to owned child processes.
On macOS only the debugger child receives the Python 3.11 framework path, optionally overridden with `CJDB_PYTHON_FRAMEWORK`.

This manifest contract builds self-contained executables and explicitly listed same-package sources.
For automatic source resolution and function drivers, see [standalone workflows](standalone.md).
For cjpm dependencies, resources, native assertions and workspace suites, use the [native package adapter](native.md) rather than flattening sources into this manifest.
InspectCJ does not translate Python benchmark tests to Cangjie.
Projects, compiler flags and native programs must be trusted local code: the controller's workspace protects original source from its own edits, not from arbitrary effects of executed programs.

## Persistent debugger session

`inspect-cj debug PROJECT --case NAME` consumes one action JSON object per input line and emits one observation per action.
It first emits a session snapshot.
`--actions FILE` reads a JSONL file instead of stdin.
A single CJDB child and inferior persist across actions until restart or session close.

```jsonl
{"action":"set_breakpoint","file":"main.cj","line":8}
{"action":"control_execution","command":"continue"}
{"action":"inspect","variable":null,"frame":0}
{"action":"inspect","variable":"total","frame":0}
{"action":"interact_code","code":"expression total + n"}
{"action":"interact_code","code":"set total = 10"}
{"action":"control_execution","command":"continue"}
{"action":"control_execution","command":"restart"}
```

The shown lines target `examples/sum/main.cj`.
Line 8 is the return statement; stopping there exposes the accumulated total before returning it.
A source breakpoint is one-shot and disappears when hit.
Blank/comment/nonstatement lines and unresolved or relocated breakpoints are rejected.
`inspect` accepts a frame index and optional variable/member/array-index path.
`interact_code` accepts one native `print`, `set` or `expression` command without command separators or control characters.
It cannot execute Python inside the controller or submit arbitrary debugger commands.

Observations include `id`, `status`, `state`, `output`, `frames`, active `breakpoints` and `live`.
A generic `unsupported expression` wrapper can conceal parse or execution errors and does not by itself establish a capability limit.
Execution signals are reported as errors, with live inspection and subsequent operations still available when CJDB restores the stop.
Expression and mutation capabilities begin unknown, reset on restart, and update from the native result of each attempted operation.
Synchronization alone does not claim expression support.
Use `doctor` to probe this installation independently of a repair.

| State | Meaning | Valid progression |
| --- | --- | --- |
| Start | Executable loaded, not yet launched | Set a breakpoint and continue |
| RuntimeState | Native process stopped at a breakpoint | Inspect, interact, continue or restart |
| RuntimeError | Recoverable interactive/inspection error while stopped | Correct the action, continue or restart |
| PostMortem | Signal/exception stop, failed exit or unavailable debugger | Inspect live frames; restart or create a new session if unavailable |
| Done | Native process exited zero | Propose a repair or restart |

Restart kills the old inferior, clears all breakpoints and mutations, resets per-process synchronization and capability state, and returns to Start.
An exited program has no live frames; no Python-style post-mortem scope is fabricated.
A debugged program reaching Done can still have wrong output.
Only full test validation establishes repair success.
A debugger transport crash closes its owned resources and reports an error; create a new session after such a transport failure.

## Repair loop and outputs

Defaults are 5 patch attempts and 20 total model calls, including both Inspector and Coder calls.
`--max-attempts` and `--max-steps` override them.
The default run/model/debug time budgets are 600/120/10 seconds, configured with `--run-timeout`, `--model-timeout` and `--debug-timeout`.
Build and case time budgets come from the manifest.
A deadline stops scheduling work when exhausted, with short bounded cleanup allowance for the debugger and subprocesses.
Processes receive group cleanup on success, failure, timeout and keyboard interruption.
The debugger and inferior have separate owned groups, and the launch ownership handoff lets the controller release ordinary inferior children even after an interrupted or failed bridge request.
Native debugger output uses a bounded FIFO, incremental UTF-8 decoding and keep-first-and-last retention; command output retains 64KiB.
Model replies are capped at 256KiB and accepted source candidates at 200000 bytes.

ProgramInspector's current-version action/observation memory is distinct from PatchCoder's previous candidate/plan/validation memory.
A new runnable code version starts a fresh Inspector memory.
For runtime failures, `propose_repair` must be the last action in a turn and include nonempty `plan` text plus `evidence` IDs from successful live inspection in that code version.
Initial build failures follow the [compiler-diagnostics path](standalone.md#compiler-errors-and-compilation-only-checks) before runtime evidence exists.
PatchCoder receives the resulting compact plan and cited observations, not the full Inspector transcript.
Both model roles receive every declared standalone case, including argv, stdin, separate stdout/stderr expectations, expected exit and comparison policy.
PatchCoder also receives these contracts on the initial compiler-diagnostics path, even without task prose.
Failed actions abort the rest of that action batch and return feedback for the next inference.
Malformed model objects and invalid source candidates consume budget and return feedback.
A failed candidate build is retained and rolled back to the last runnable source for further inspection.
Every runnable candidate receives the entire configured test suite.

See the [artifact reference](automation.md#artifacts-and-acceptance) for output contents and [guarded application and replay](standalone.md#artifacts-replay-and-application) for reviewing and applying candidates.
The [machine outcomes table](standalone.md#machine-outcomes) owns repair statuses and exit codes; [automation](automation.md#noninteractive-execution) describes test and argument-error exits.
After a session opens, debug exits 0 when all requested actions succeed, 2 for invalid or failed actions and 3 for unsupported actions.
For startup outcomes, see [native discovery and launch requirements](native.md#authoritative-behavior-references) and [standalone executable-case requirements](standalone.md#compiler-errors-and-compilation-only-checks).
Standalone debug actions can report unsupported while the rest of the native integration remains functional.

## Model configuration

`--transport codex` uses the existing authenticated Codex CLI with `exec --ignore-user-config --sandbox read-only --ephemeral --output-schema`.
No authentication is created or changed by this tool.
The prompt asks the model to return actions/source as JSON and use no external tools.
`--model` is optional for this transport and never changes account pins.
Actual credentials and subprocess diagnostic output are excluded from report metadata.
Known secret environment values are redacted from transport error messages.

`--transport responses --model MODEL` uses `OPENAI_API_KEY` with the Responses API and a strict JSON schema.
The request sets `store: false` and sends source, requirement and selected runtime evidence as model context.
Requests use the official `https://api.openai.com/v1/responses` endpoint.
HTTP redirects are rejected so bearer credentials are not forwarded to redirected hosts.
HTTP runs in a bounded child process so a stalled or slow-drip server cannot retain the controller indefinitely.
The HTTP contract is tested with a local test harness; credential-dependent external API access is separate from the demonstrated Codex transport.

`--transport scripted --script FILE` reads a JSON array of `{ "role": "inspector" | "coder", "response": {...} }` records.
Roles must match the loop's requested role and exhaustion is an explicit failure.
See `examples/sum/offline.json` for a runnable replay.
Scripted mode still uses actual Cangjie compilation, CJDB and full validation.
It is labelled offline in reports and must never be described as LLM validation.

## Platform capabilities

The [official CJDB manual](https://docs.cangjie-lang.cn/en/docs/1.0.0/tools/tools/cjdb_manual_cjnative.html) describes native debug symbols and print/set/expression operations, and notes macOS expression limitations in that release.
The earlier blanket macOS expression-failure conclusion was disproven.
This installed Cangjie 1.1.0 debugger can omit the loaded runtime from its module table, preventing JIT runtime-symbol lookup.
InspectCJ now synchronizes that missing metadata before forwarding a general `expression` command.
The adapter reports observed capabilities instead of disabling expressions or mutations by platform.
On Linux, set `CJC` and `CJDB` to the matching native SDK tools, run `doctor`, then run the same native suite with `INSPECT_CJ_NATIVE=1`.
Successful expression or mutation probes are required before claiming support on that installation.
Linux execution has not been tested on this macOS delivery machine.
Do not alter SDK/debugserver binaries or disable platform security to make a test pass.

## Runtime module synchronization

On Darwin targets, InspectCJ first checks the existing runtime mapping and required symbol load addresses.
When metadata is incomplete, it asks the existing debugserver for structured loaded images with `jGetLoadedDynamicLibrariesInfos:{"fetch_all_solibs":true}`.
It matches the runtime path, UUID and architecture against the local SB module and the live inferior's Mach-O header.
Each missing section address is its SB file address plus the measured header slide, rather than an assumed ASLR base.
Correct mappings are retained; conflicting mappings fail explicitly instead of being overwritten.
The required safepoint and exception-wrapper symbols must have verified load addresses before the user expression is submitted.
The helper executes no probe expression and never retries the user's expression, including state-changing calls.

Discovery accepts at most 512000 response bytes and 512 images, and registration examines at most 64 top-level sections.
The packet timeout is temporarily bounded to two seconds and restored afterward, with a four-second checked synchronization budget and the existing outer debugger deadline.
Budget checks run after each memory read and before successful synchronization hands the expression to CJDB.
These checks cannot preempt a blocked native call; an over-budget read is rejected when it returns, and the outer controller deadline remains the final interrupt bound.
Failures are bounded and cached for the current process; the observation explains that the expression was not executed.
Restart removes only unchanged metadata owned by the helper and clears its failure cache.
Linux keeps its native loader/evaluator path and does not issue the Darwin packet.
No SDK binary, debugger internal, initialization file, security setting or system default is modified.

Do not interpret a successful arithmetic expression as proof that all expression forms work.
See the [tested evaluator matrix](validation.md#native-evaluator-behavior) for supported scenarios, the remaining array-index execution fault and platform limits.
