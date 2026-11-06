# Standalone repair

Install the CLI and matching SDK as described in [Getting started](../GETTING_STARTED.md).
Run `inspect-cj doctor` to prove native breakpoints and inspection on your machine.

## Program arguments and exact output

```sh
inspect-cj repair main.cj --task 'Print the inclusive sum from 1 through n' \
  --expect-line 10 -- 4
```

Everything after `--` is a program argument, without shell interpretation by InspectCJ.
Each `--expect-line` adds its literal text and exactly one newline.
Use `--expect-stdout` for an exact string including newlines, `--expect-stderr` for stderr, and `--expect-exit` for an explicit expected exit status.
New cases default to exit zero and empty stderr.
Supplying only an exit expectation leaves stdout unchecked while still requiring empty stderr, so prefer an immutable assertion driver or explicit output whenever possible.
See the [manifest comparison contract](usage.md#project-contract) for legacy combined matching and separate exact stream checks.

```sh
printf '4\n' > input.txt
inspect-cj repair main.cj --task 'Double the input integer' \
  --stdin-file input.txt --expect-line 8
printf '%s\n' 'Preserve the API and double the input' | \
  inspect-cj repair main.cj --task - --stdin-file input.txt --expect-line 8
```

`--task -` reads task text from CLI stdin, whereas `--stdin-file -` reads program input.
These two stdin consumers are mutually exclusive.
Input must be UTF-8 and no larger than 64KiB.

## Functions and independent expectations

```sh
inspect-cj repair calculation.cj --driver tests/repro.cj \
  --task 'Clamp indices to the valid inclusive range' --expect-line 2 -- 5 3
```

For a simple function expression, generate a scratch driver with independently supplied expected behavior:

```sh
inspect-cj driver --expression 'boundedIndex(5, 3)' --expect-line 2 --output /tmp/repro.cj
inspect-cj repair calculation.cj --driver /tmp/repro.cj --expect-line 2
```

The helper prints the next invocation as an argv array, never executes shell text, and refuses existing output files.
Review the generated driver before repair.
Its expression is supplied by you; its expected result is never inferred from program output.
The driver is immutable and must be a different .cj file.
For a driver containing independent assertions, `--expect-exit 0` can preserve its assertion oracle without requiring prose or output.
The implementation and driver must compile together as one native executable.
An explicit file target includes only that file and its explicit driver.
A directory without a manifest is inferred only when it has exactly one immediate .cj file.
Multiple source candidates require an explicit target; recursive source flattening is never inferred.

No expected behavior is guessed from the current output.
Without independent acceptance, repair returns JSON `needs_input`, exit 5 and a `missing` list, with no model call.
`--non-interactive` is available for editor/CI callers; the CLI never waits for an input wizard.

## Compiler errors and compilation-only checks

Initial compiler errors are sent to the Coder with actual diagnostics and no runtime Inspector evidence.
The same attempt/time allowance applies to rebuilding and subsequent semantic validation.
Once a candidate executes, runtime failures require real cited Inspector observations as before.

```sh
inspect-cj repair main.cj --task 'Print 42' --expect-line 42
inspect-cj repair main.cj --compile-only --task 'Resolve the compiler error'
```

An explicit compilation-only run reports `compile_only`, never semantic success.
This also applies to `inspect-cj test project.json` with a compilation-only contract.
Debugging requires a declared executable case; an empty standalone contract returns `needs_input`, exit 5 and `missing: ["case"]` before compilation or debugger launch.
A runtime repair reports success only after every declared executable case passes.
Use project.json to declare multiple independent regression cases for standalone programs.

## Artifacts, replay and application

The default output is a fresh private directory under `${XDG_CACHE_HOME:-$HOME/.cache}/inspect-cj/runs`.
Use `--output NEW_DIRECTORY` for caller-controlled artifacts; an existing directory is refused.
Original source files remain unchanged during repair.
See the [artifact reference](automation.md#artifacts-and-acceptance) for run contents.
The generated manifest describes the resolved contract; it does not need to live beside your sources.
The invocation records canonical source/cwd paths, source and compiler/debugger hashes, stdin bytes, arguments and acceptance identity.
Treat the run directory as sensitive source/input material.

```sh
inspect-cj repair --replay /absolute/path/to/previous/run --non-interactive
cat /absolute/path/to/run/candidate.diff
inspect-cj apply /absolute/path/to/run
```

Replay revalidates sources, input fixtures, acceptance and toolchain identity, then executes a fresh run.
It does not reuse a historical passing result.
Apply is explicit, requires a validated candidate, checks target path and both base/candidate hashes, stages beside the source and replaces it atomically while retaining its file mode.
A changed source or symlink target is refused.
Apply never commits or merges Git changes.

## Machine outcomes

| Status | Exit | Meaning |
| --- | --- | --- |
| success | 0 | All declared semantic cases passed |
| compile_only | 0 | Explicit compilation check passed |
| failed | 2 | Configuration, tool or transport failure |
| unsupported | 3 | Required native toolchain unavailable |
| exhausted | 4 | Shared attempt/step/time allowance exhausted |
| needs_input | 5 | Missing independent acceptance or ambiguous target |
| interrupted | 130 | Repair interrupted and owned subprocesses cleaned up |

All repair/test CLI output, including argument errors, is JSON except help.
A model transport can be selected using the existing `--transport codex`, explicit Responses model, or bounded offline script.
See [model configuration](usage.md#model-configuration) for authentication reuse, child configuration and per-run model selection.
