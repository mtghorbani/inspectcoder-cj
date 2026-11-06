# CLI, CI and coding agents

Install InspectCJ and the matching SDK using [Getting started](../GETTING_STARTED.md).
Use an absolute installed executable path in automation and select SDK paths through the [saved configuration](../GETTING_STARTED.md#3-save-sdk-selection-and-check-it).
Optional environment overrides should apply only to the CLI child environment.
Authentication must already work with your selected transport; never put a provider secret in command arguments or checked-in settings.
No daemon, MCP wrapper or editor is required.

## Noninteractive execution

```sh
/absolute/path/to/inspectcoder-cj repair /absolute/path/to/main.cj \
  --task 'Print the inclusive sum from 1 through n' --expect-line 10 \
  --non-interactive --run-timeout 600 --max-attempts 5 --max-steps 20 -- 4
/absolute/path/to/inspectcoder-cj repair /absolute/path/to/cjpm-project \
  --non-interactive --run-timeout 600
```

Native assertions supply acceptance without task prose.
Missing acceptance or ambiguous sources return needs_input with missing/choices and exit 5, without waiting for a prompt or asking a model to invent behavior.
Commands emit JSON except help and `--version`; debug emits JSONL observations.
Argparse usage failures also emit JSON with failed status and exit 2.
Do not infer semantic success from exit zero alone: compile_only is intentionally distinct.
`test` returns 0 for complete passing cases or an explicit [compilation-only check](standalone.md#compiler-errors-and-compilation-only-checks), 1 for assertion failures and 2 for build/configuration errors.
Repair statuses/exits are documented in [standalone workflows](standalone.md#machine-outcomes).
Unsupported native layouts/toolchains return exit 3.

Use subprocess argv arrays to preserve spaces, quotes and metacharacters literally:

```python
import json
import subprocess
import sys

# Run as: python3 ci_repair.py /absolute/inspect-cj /absolute/cjpm-project
completed = subprocess.run(
    [sys.argv[1], "repair", sys.argv[2], "--non-interactive", "--run-timeout", "600"],
    stdin=subprocess.DEVNULL,
    text=True,
    capture_output=True,
    check=False,
)
print(completed.stdout, end="")
print(completed.stderr, end="", file=sys.stderr)
result = json.loads(completed.stdout)
if completed.returncode != 0 or result["status"] != "success":
    raise SystemExit(completed.returncode or 2)
print("Review candidate:", result["output"])
```

Set the CI job timeout above the CLI deadline to leave time for cancellation and artifact collection.
Cancel by delivering SIGINT to the CLI and waiting for it to finish cleanup.
Repair records interrupted status and exits 130; owned compiler/test/debugger/model descendants are cleaned on cancellation and timeout.
An external SIGKILL cannot produce a final JSON receipt.
The allowance is shared across model, compile, native discovery/test and debug work, rather than renewed for each nested operation.

## Artifacts and acceptance

Pass `--output NEW_DIRECTORY` when CI needs a deterministic artifact location; reuse is refused.
Otherwise read the automatically selected private output path from the final result.
Preserve these artifacts according to your source/input privacy policy:

| Artifact | Meaning |
| --- | --- |
| invocation.json | Schema 1, canonical roots, input/source/toolchain/engine hashes and acceptance identity |
| project.json | Resolved execution/acceptance contract, including native adapter metadata |
| events.jsonl | Model responses, real debugger observations and validation events |
| report.json | Final status, reason, counters, every build and complete suite iteration |
| versions/ | Original and proposed implementation versions |
| candidate.diff, repaired.cj, candidate.json | Successful candidate, review diff and guarded application metadata |
| build/ | Real build logs, native discovery/execution XML, tool versions and exact native launch metadata |
| workspace/ | Private snapshot of declared immutable project inputs and editable candidate |

A failed run has no applicable candidate.
Test skips, incomplete discovery, changed membership and failed runner exit semantics prevent a complete native success claim.
Replay validates recorded inputs and toolchain, then executes a fresh run.
Apply checks base/candidate hashes, canonical target path and immutable acceptance inventory before atomic source replacement.
New native input files also invalidate a receipt.

```sh
/path/to/inspect-cj repair --replay /absolute/run --non-interactive
/path/to/inspect-cj apply /absolute/reviewed/run
/path/to/inspect-cj test /absolute/cjpm-project
```

Keep apply as an explicit authorized step in CI or agent automation.
The CLI never commits, merges or publishes the candidate.

## Coding-agent setup

The repository ships [.agents/skills/inspect-cj/SKILL.md](../.agents/skills/inspect-cj/SKILL.md).
A compatible agent can discover it directly from this checkout or load that exact file for this project.
Keep its relative documentation links with the repository; no global skill or credential installation is needed.
Provide the absolute CLI and target paths, intended behavior when no assertions exist, and one editable implementation scope when needed.
The skill uses the shared CLI and its real validation artifacts; it cannot turn a compiler-only result into semantic proof.

## Supported environment and troubleshooting

The current native cjpm certification is macOS arm64 with SDK/cjpm/CJDB 1.1.0.
The standalone native suite is verified on the same host; Linux instructions are setup guidance, not a Linux execution claim.
For macOS Python linkage and compatible Apple SDK selection, follow [Getting started](../GETTING_STARTED.md#3-save-sdk-selection-and-check-it).
Do not modify Xcode defaults or SDK binaries.
Use doctor output to distinguish missing tools from native debugger capability errors.
An evaluator failure, including the known SDK array-expression ABI fault, must remain explicit; use direct inspection and compiled controls without changing the acceptance oracle.
Transport failures require fixing the existing authentication/access or selecting an already authorized transport, not changing shared account/model defaults.

## Optional editor process tasks

[examples/automation/tasks.json](../examples/automation/tasks.json) is a process-task example for the same CLI, with literal argument entries and independent expected-line input.
Copy the task definitions into your own task configuration if useful; they do not replace the installable extension's diagnostics, cancellation and diff/apply commands.
Set inspectCJ.executable to the absolute CLI first.
Process tasks use the same [saved SDK configuration](../GETTING_STARTED.md#3-save-sdk-selection-and-check-it) as the CLI.
Optional environment overrides for a task belong in its options.env or inherited terminal environment; the extension's child environment setting applies to extension commands.
