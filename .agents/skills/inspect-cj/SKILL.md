---
name: inspect-cj
description: Use when repairing a Cangjie source file or failing cjpm native test with the installed InspectCJ CLI; use existing assertions or independently supplied expected behavior.
---

# Repair Cangjie with InspectCJ

Use the existing CLI, not a replacement debugger/model loop or service.
Use the installed CLI's absolute executable path as described in [setup](../../../GETTING_STARTED.md).
Run `doctor` when toolchain capability is unknown.
Read [setup](../../../GETTING_STARTED.md), [standalone](../../../docs/standalone.md) or [native packages](../../../docs/native.md) for the selected target.
Paths in these references are relative to this skill directory.

For a package, run `inspect-cj test /absolute/package` and preserve its native assertions, fixtures, resources and locks.
Use its discovered opaque identity with `--case` if a specific failure matters.
Use `--editable` for one ambiguous implementation file.
For standalone code, use the user's independent expected stdout/stderr/exit or immutable assertion driver.
An observed failing result is evidence of the bug, never an expected-output oracle.
If independent behavior is absent, report the CLI's needs_input fields; do not guess a result.

```sh
/absolute/path/to/inspectcoder-cj repair /absolute/package \
  --non-interactive --run-timeout 600 --max-attempts 5 --max-steps 20
```

Invoke through an argv array when using subprocess APIs, with program arguments after `--`.
Use the existing authenticated transport and pinned model configuration.
Keep the single shared timeout and attempt allowance bounded.
Do not change SDK binaries, global Python, authentication, shell configuration or security settings to make a run pass.

Read the returned JSON status and output path.
Success means all declared semantic cases passed; compile_only proves compilation only.
Treat unsupported, exhausted, needs_input and interrupted as incomplete repair outcomes.
Inspect `candidate.diff`, `report.json` and actual debugger/native test evidence.
A targeted pass cannot replace full-suite acceptance.
Report evaluator errors separately from failed program assertions.

Originals remain unchanged during repair.
When applying is within the user's task authorization, invoke `inspect-cj apply RUN` explicitly after reviewing the candidate.
A stale source or acceptance refusal requires a fresh run, never a forced copy.
Otherwise present the candidate and its validation for review.
Rerun the declared acceptance suite after application.
Replay uses `inspect-cj repair --replay RUN --non-interactive` and checks current inputs/toolchain before fresh validation.
See [automation](../../../docs/automation.md) for statuses, cancellation and artifact handling.
