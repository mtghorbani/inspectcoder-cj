# Validation and platform limits

See [Contributing](../CONTRIBUTING.md#checks) for reproducible test, lint, formatting, and package checks.
The [CI workflow](../.github/workflows/checks.yml) runs portable checks and builds release packages.
Native compiler, debugger, model-provider, and editor-host checks require their documented prerequisites and opt-in settings.
A skipped integration check does not establish support for that environment.

## Native evaluator behavior

The following behavior was observed with Cangjie SDK/cjc/cjpm 1.1.0 and its matching CJDB on Darwin 25.6.0 arm64.
These observations describe that environment; they do not certify other SDK versions or platforms.
See [setup](../GETTING_STARTED.md) for toolchain configuration and [runtime module synchronization](usage.md#runtime-module-synchronization) for evaluator preparation.

| Program/scenario | Observed behavior |
| --- | --- |
| Original sum | `expression total + n` returns 10 at n=4,total=6 |
| Typed function | Literal 3, constant arithmetic 3, local arithmetic 8, comparison/boolean true, float 3.75, member 9 |
| Loop and lexical block | Loop/local arithmetic 5 and lexical arithmetic 106; selected caller argument arithmetic 24 |
| Recursion | Selected frames return 2, 12, 13 and 11 for their corresponding local/argument expressions |
| Repeated expressions and side effects | Repeated arithmetic stable; one `bump()` increments counter once; assignment and `set` affect only the live process |
| Restart/new processes | Runtime registrations and capability evidence reset per launch; original values return |
| Correct/missing metadata | Existing mappings stay byte-identical in module listings; clearing runtime metadata is repaired |
| Conflicting metadata | Expression is not executed; the conflicting address is retained and failure stays cached |
| General array index | Runtime evaluation errors with SIGSEGV or SIGBUS; index inspection and later arithmetic remain usable; grammar support is not inferred |

The evaluator matrix uses the installed compiler, debugserver, and Cangjie evaluator.
Deterministic boundary tests separately cover missing and conflicting metadata, UUID and architecture checks, malformed discovery responses, address validation, synchronization deadlines, cleanup, and prevention of expression replay.
Synchronization does not alter SDK binaries or debugger security settings.
An array-expression execution fault does not establish that indexing syntax is unsupported or that all expressions are unavailable.

## Scope and limitations

Linux setup is documented, but native execution on Linux has not been verified.
Other SDK versions, operating systems, and editor platforms require their own integration checks.
Standalone source files and cjpm packages use the workflows described in [standalone](standalone.md) and [native packages](native.md).

The paper's benchmark datasets, private-test evaluation, model comparisons, and published performance improvements have not been reproduced for Cangjie.
Scripted repair tests establish deterministic orchestration behavior; authenticated model-provider access must be checked separately.
Portable CI does not certify native debugger behavior or live model-provider access.
