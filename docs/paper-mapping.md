# Paper and reference mapping

This implementation follows Yunkun Wang et al., *InspectCoder: Dynamic Analysis-Driven Self Repair through Interactive LLM-Debugger Collaboration*, PACMPL 10, OOPSLA1, article 130, April 2026, [DOI 10.1145/3798238](https://doi.org/10.1145/3798238).
The paper's Python benchmark performance claims are not claims about this Cangjie implementation.

The public [reference repository](https://github.com/greenlight2000/InspectCoder/) was studied at commit `f64730e57b83f74aceb22cbdab6d9bd2cdb9bb0e`.
The study covered `inspectcoder.py`, `inspectware.py`, `agent_prompts.py`, `utils.py`, `main.py`, configuration, representative BigCodeBench/LiveCodeBench test records, and case-study action/observation logs.
There is no standalone automated test suite in that snapshot; tests are embedded in benchmark data and depend on external Python benchmark packages.
Its external `request_model.LLM`, PDB terminal, Python test assembly and data-science dependencies are replaced with native contracts here.
Reference code is not vendored or executed.

| Paper/reference concept | This implementation | Verification |
| --- | --- | --- |
| Sections 2.2-2.4, dual agents and separate memories | `repair.py` keeps current Inspector observations separate from cumulative Coder patch history | Two-attempt native repair inspects new failing test and checks context separation |
| Section 2.3.1, temporary source breakpoint | `debugger.py` validates source; `cjdb_bridge.py` creates an exact-location SB one-shot breakpoint | Native breakpoint disappears after its first hit |
| Continue and restart | Persistent native inferior with SB process states; restart kills it and clears breakpoints | Native continuation and mutation reset tests |
| `interact_code` | Cangjie print/set/expression routed to the installed language plugin | [Native evaluator matrix](validation.md#native-evaluator-behavior) |
| `propose_repair` and section 2.3.3 root-cause report | Nonempty plan must cite successful current-version runtime observation IDs | Ungrounded plan is rejected until inference budget exhausts |
| Section 2.4.2, full validation and new failing test | Every candidate builds and runs every manifest case; first failure becomes focus | A partial patch passes four but fails one; the next version fixes all cases |
| Section 2.5.1, Start/RuntimeState/RuntimeError/PostMortem/Done | Explicit middleware states based on structured SB process state and frame validity | Native invalid actions, recoverable variable errors, failure exit and restart |
| Section 2.5.2, reversible perturbation | Native `set` changes the inferior only; source remains untouched | `total=6` becomes 10, continuation prints 10, restart restores 6 |
| Section 2.5.3, context and output filtering | Structured frames/breakpoints plus bounded first/last output | Flood/timeout tests preserve output edges and kill processes |
| Python executable gist/test assembly | cjc debug build of configured Cangjie sources with argument or stdin cases | Sum, separate function driver and stdin native examples |
| Section 3.5, 5 patches and 20 inference steps | Configurable limits with both model roles counted in the total | Budget and multi-patch loop tests |
| Reference prompts' inspection and experiment strategies | New native prompts in `models.py` ask for upstream state tracing and evidence-based perturbation | Scripted and separately recorded real Codex repair demonstrations |

## Deliberate adaptations

The native Start state means loaded but unlaunched, since a universal first source line is not available for all native executables.
Native failed exits cannot retain Python-style live post-mortem locals; exited frames are explicitly absent.
A live signal stop may still expose real frames for inspection.
General Python REPL injection cannot be faithfully transplanted to compiled Cangjie.
Native expression and mutation support depends on the installed CJDB plugin and platform, so unavailable operations return unsupported rather than fabricated execution.
[Session-only runtime synchronization](usage.md#runtime-module-synchronization) repairs the native integration boundary and preserves the paper's perturbation workflow.
See the [validation record](validation.md#native-evaluator-behavior) for observed evaluator behavior and remaining limits.

The controller returns JSON contracts instead of parsing model-written Python call strings or evaluating benchmark metadata with `eval`.
It preserves exact candidate sources, build diagnostics and complete suite results while limiting the context sent to the model.
Original project sources are copied into a new run workspace and are not overwritten by controller edits.
The complete paper benchmarks, private test split evaluation, model comparisons and reported efficiency gains are outside this functional native tool's validation scope.
