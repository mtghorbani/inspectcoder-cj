"""The inspect -> evidence plan -> patch -> complete validation loop."""

from __future__ import annotations

import json
import shutil
import stat
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .artifacts import digest, finish_candidate, record_invocation
from .debugger import CJDBSession
from .jobs import NeedsInput
from .models import InvalidModelResponse, Model, redact
from .native import UnsupportedWorkflow
from .project import Project, TestResult, Toolchain, positive, run_suite


class BudgetExhausted(RuntimeError):
    pass


@dataclass(frozen=True)
class RepairResult:
    status: str
    reason: str
    output: str
    patch_attempts: int
    inference_steps: int
    missing: list[str] | None = None
    choices: list[str] | None = None


class RepairController:
    def __init__(
        self,
        project: Project,
        model: Model,
        output: Path,
        *,
        tools: Toolchain | None = None,
        max_attempts: int = 5,
        max_steps: int = 20,
        run_timeout: float = 600,
        model_timeout: float = 120,
        debug_timeout: float = 10,
    ):
        for name, value in (("max_attempts", max_attempts), ("max_steps", max_steps)):
            if type(value) is not int or not 1 <= value <= 1000:
                raise ValueError(f"{name} must be an integer from 1 to 1000")
        self.project, self.model = project, model
        self.output = output.resolve()
        self.tools = tools or Toolchain.discover(project.sysroot)
        self.max_attempts, self.max_steps = max_attempts, max_steps
        self.run_timeout = positive(run_timeout, "run timeout")
        self.model_timeout = positive(model_timeout, "model timeout")
        self.debug_timeout = positive(debug_timeout, "debug timeout")
        self.inference_steps = 0
        self.patch_attempts = 0
        self.inspector_memory: list[dict] = []
        self.coder_memory: list[dict] = []
        self.iterations: list[dict] = []

    def _remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BudgetExhausted("run time budget exhausted")
        return remaining

    def _event(self, event: dict) -> None:
        with (self.output / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _ask(self, role: str, context: dict) -> dict:
        if self.inference_steps >= self.max_steps:
            raise BudgetExhausted("inference step budget exhausted")
        self.inference_steps += 1
        # Copies protect the separate memories from transport-side mutation.
        context = json.loads(json.dumps(context))
        for key in ("memory", "observations"):
            while len(json.dumps(context).encode()) > 128000 and len(context.get(key, [])) > 2:
                context[key].pop(1)
        if len(json.dumps(context).encode()) > 128000:
            raise BudgetExhausted("model context budget exhausted (128000 bytes)")
        try:
            response = self.model.complete(
                role, context, timeout=min(self.model_timeout, self._remaining())
            )
            if not isinstance(response, dict):
                raise InvalidModelResponse("model response must be a JSON object")
        except InvalidModelResponse as error:
            response = {"error": redact(str(error))}
        self._remaining()
        self._event(
            {"type": "model", "role": role, "step": self.inference_steps, "response": response}
        )
        return response

    def _sources(self, project: Project, *, numbered: bool) -> dict[str, str]:
        sources = {}
        for name in project.sources:
            text = (project.root / name).read_text()
            sources[name] = (
                "\n".join(f"{i}| {line}" for i, line in enumerate(text.splitlines(), 1))
                if numbered
                else text
            )
        return sources

    def _inspect(self, project: Project, executable: Path, failure: TestResult) -> dict:
        self._restore_acceptance(project)
        self.inspector_memory = []
        evidence: dict[str, dict] = {}
        if failure.launch:
            from .project import TestCase

            case = TestCase(failure.name, failure.launch["args"], "", None)
            executable = Path(failure.launch["executable"])
        else:
            case = next(test for test in project.tests if test.name == failure.name)
        with CJDBSession(
            project,
            executable,
            case,
            tools=self.tools,
            timeout=min(self.debug_timeout, self._remaining()),
            deadline=self.deadline,
            launch=failure.launch,
        ) as debugger:
            while True:
                response = self._ask(
                    "inspector",
                    {
                        "requirement": project.requirement,
                        "tests": [asdict(test) for test in project.tests],
                        "sources": self._sources(project, numbered=True),
                        "focused_test": asdict(case),
                        "failure": asdict(failure),
                        "state": debugger.snapshot(),
                        "memory": self.inspector_memory,
                        "observations": list(evidence.values()),
                        "steps_remaining": self.max_steps - self.inference_steps,
                    },
                )
                actions = response.get("actions")
                if (
                    not isinstance(actions, list)
                    or not 1 <= len(actions) <= 8
                    or not isinstance(response.get("thought"), str)
                ):
                    self.inspector_memory.append(
                        {"error": "Return thought text and 1 to 8 actions"}
                    )
                    continue
                for index, action in enumerate(actions):
                    self._remaining()
                    if isinstance(action, dict) and action.get("action") == "propose_repair":
                        plan, citations = action.get("plan"), action.get("evidence")
                        valid = (
                            isinstance(plan, str)
                            and bool(plan.strip())
                            and isinstance(citations, list)
                            and bool(citations)
                            and all(isinstance(key, str) and key in evidence for key in citations)
                            and index == len(actions) - 1
                        )
                        if valid:
                            return {"plan": plan, "evidence": [evidence[key] for key in citations]}
                        self.inspector_memory.append(
                            {
                                "error": "propose_repair must be last and cite "
                                "successful live inspection evidence IDs"
                            }
                        )
                        break
                    observation = debugger.execute(action)
                    if (
                        observation.status == "ok"
                        and action.get("action") == "control_execution"
                        and action.get("command") == "restart"
                    ):
                        self._restore_acceptance(project)
                    record = {"action": action, "observation": observation.to_dict()}
                    self.inspector_memory.append(record)
                    self._event({"type": "debugger", "version": self.patch_attempts, **record})
                    if (
                        observation.status == "ok"
                        and observation.live
                        and isinstance(action, dict)
                        and action.get("action") in ("inspect", "interact_code")
                    ):
                        evidence[observation.id] = observation.to_dict()
                    if observation.status != "ok":
                        break

    def _check_acceptance(self, project: Project) -> None:
        for name, (expected, _) in self.acceptance.items():
            path = project.root / name
            if path.is_symlink() or not path.is_file() or path.read_bytes() != expected:
                raise UnsupportedWorkflow(f"immutable native acceptance input changed: {name}")

    def _restore_acceptance(self, project: Project) -> None:
        editable = project.root / project.editable
        files = {
            **self.acceptance,
            project.editable: (editable.read_bytes(), stat.S_IMODE(editable.stat().st_mode)),
        }
        shutil.rmtree(project.root)
        project.root.mkdir()
        for name, (contents, mode) in files.items():
            path = project.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
            path.chmod(mode)

    def _validate(self, project: Project, version: int) -> tuple[Path, list[TestResult], bool]:
        project = replace(project, build_timeout=min(project.build_timeout, self._remaining()))
        self._check_acceptance(project)
        self._restore_acceptance(project)
        executable, build = project.build(
            self.output / "build" / str(version), self.tools, deadline=self.deadline
        )
        results = []
        if build.returncode == 0 and not build.timed_out:
            results = run_suite(project, executable, self.tools, deadline=self.deadline)
        self._check_acceptance(project)
        failure = next((case for case in results if not case.passed), None)
        self.iterations.append(
            {
                "version": version,
                "build": asdict(build),
                "executable_sha256": (
                    digest(executable) if executable.is_file() and not build.returncode else None
                ),
                "tests": [asdict(case) for case in results],
                "focused_test": failure.name if failure else None,
            }
        )
        self._event({"type": "validation", **self.iterations[-1]})
        if project.native and results:
            if any(case.timed_out for case in results):
                raise BudgetExhausted("native suite timed out")
            if any(case.skipped for case in results):
                raise UnsupportedWorkflow("native suite coverage is partial: skipped cases remain")
            identities = {case.name for case in results}
            if version == 0:
                self.native_identities = identities
                selected = project.native.get("case")
                if (
                    selected
                    and any(case.name == selected and case.passed for case in results)
                    and any(not case.passed for case in results)
                ):
                    raise NeedsInput(
                        "Selected native case already passes",
                        ["case"],
                        [case.name for case in results if not case.passed],
                    )
            elif hasattr(self, "native_identities") and identities != self.native_identities:
                raise UnsupportedWorkflow("declared native suite identities changed")
            elif not hasattr(self, "native_identities"):
                self.native_identities = identities
        return executable, results, build.returncode == 0 and not build.timed_out

    def run(self) -> RepairResult:
        self.output.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.deadline = time.monotonic() + self.run_timeout
        record_invocation(self.project, self.tools, self.output)
        workspace = self.output / "workspace"
        project = self.project.snapshot(workspace)
        self.acceptance = {
            name: (
                (workspace / name).read_bytes(),
                stat.S_IMODE((workspace / name).stat().st_mode),
            )
            for name in sorted(set(project.files) | set(project.sources))
            if name != project.editable
        }
        versions = self.output / "versions"
        versions.mkdir()
        current = (workspace / project.editable).read_text()
        (versions / "0.cj").write_text(current)
        status, reason = "failed", "initial native build failed"
        missing, choices = None, None
        try:
            executable, results, built = self._validate(project, 0)
            while True:
                if built and all(case.passed for case in results):
                    status, reason = (
                        ("compile_only", "compiled; no semantic acceptance was requested")
                        if project.compile_only
                        else ("success", "all configured tests passed")
                    )
                    (self.output / "repaired.cj").write_text(current)
                    break
                if self.patch_attempts >= self.max_attempts:
                    raise BudgetExhausted("patch attempt budget exhausted")
                failure = next((case for case in results if not case.passed), None)
                if built:
                    try:
                        repair_plan = self._inspect(project, executable, failure)
                    finally:
                        self._restore_acceptance(project)
                else:
                    if self.iterations[-1]["build"]["timed_out"]:
                        raise BudgetExhausted("native build timed out")
                    repair_plan = {
                        "mode": "compiler_diagnostics",
                        "diagnostics": self.iterations[-1]["build"],
                        "plan": "Fix the actual compiler diagnostics, preserving the acceptance "
                        "contract. No runtime Inspector evidence exists before compilation.",
                    }
                self.patch_attempts += 1
                response = self._ask(
                    "coder",
                    {
                        "requirement": project.requirement,
                        "tests": [asdict(test) for test in project.tests],
                        "sources": self._sources(project, numbered=False),
                        "editable": project.editable,
                        "failure": asdict(failure) if failure else None,
                        "mode": "runtime" if built else "compiler_diagnostics",
                        "repair_plan": repair_plan,
                        "memory": self.coder_memory,
                    },
                )
                source = response.get("source")
                memory = {
                    "version": self.patch_attempts,
                    "repair_plan": repair_plan,
                    "source": source,
                    "summary": response.get("summary", ""),
                }
                if (
                    not isinstance(source, str)
                    or not source.strip()
                    or len(source.encode()) > 200000
                ):
                    memory["error"] = "Return a nonempty source string of at most 200000 bytes"
                    self.coder_memory.append(memory)
                    continue
                (versions / f"{self.patch_attempts}.cj").write_text(source)
                (workspace / project.editable).write_text(source)
                candidate, tests, candidate_built = self._validate(project, self.patch_attempts)
                memory["validation"] = self.iterations[-1]
                self.coder_memory.append(memory)
                if candidate_built or not built:
                    current, executable, results, built = source, candidate, tests, candidate_built
                else:
                    # Preserve the failed candidate as evidence; only inspect runnable code.
                    (workspace / project.editable).write_text(current)
            self._remaining()
        except NeedsInput as error:
            status, reason = "needs_input", str(error)
            missing, choices = error.missing, error.choices
        except UnsupportedWorkflow as error:
            status, reason = "unsupported", str(error)
        except KeyboardInterrupt:
            status, reason = "interrupted", "cancelled; owned processes cleaned up"
        except BudgetExhausted as error:
            status, reason = "exhausted", str(error)
        except FileNotFoundError as error:
            status, reason = "unsupported", redact(str(error))
        except (OSError, RuntimeError, ValueError) as error:
            status, reason = "failed", redact(str(error))
        result = RepairResult(
            status,
            reason,
            str(self.output),
            self.patch_attempts,
            self.inference_steps,
            missing,
            choices,
        )
        if status in ("success", "compile_only"):
            finish_candidate(self.project, self.output)
        report = {
            **asdict(result),
            "transport": self.model.label,
            "iterations": self.iterations,
            "patch_memory": self.coder_memory,
        }
        (self.output / "report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        )
        return result
