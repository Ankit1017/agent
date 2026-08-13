"""Deterministic situation-based workflow catalog, selection, and state transitions."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal, cast

from local_harness.domain.errors import ToolExecutionError
from local_harness.domain.models import (
    Session,
    TaskPlan,
    TaskStep,
    WorkflowCompletionRule,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowSelection,
    WorkflowStageDefinition,
    WorkflowStageRun,
)

WorkflowMode = Literal["auto", "off"]


def _stage(
    stage_id: str, description: str, *tools: str, required: bool = True
) -> WorkflowStageDefinition:
    return WorkflowStageDefinition(stage_id, description, tuple(tools), required)


WORKFLOWS: tuple[WorkflowDefinition, ...] = (
    WorkflowDefinition(
        "project_orientation",
        "Project orientation",
        "Explain project architecture and entry points.",
        1,
        (
            "understand project",
            "project architecture",
            "project structure",
            "orient",
            "entry points",
        ),
        (),
        70,
        (
            _stage("memory", "Retrieve project architecture", "project_memory", "inspect_project"),
            _stage("inspect", "Inspect manifests and entry points", "inspect_project"),
            _stage(
                "dependencies", "Inspect dependency facts", "dependency_context", required=False
            ),
            _stage("changes", "Inspect current project changes", "changed_context", required=False),
        ),
        suggested_call_budget=8,
    ),
    WorkflowDefinition(
        "locate_code",
        "Locate code",
        "Find exact definitions and references.",
        1,
        ("where is", "locate", "find implementation", "find definition", "references"),
        (),
        80,
        (
            _stage("memory", "Retrieve relevant symbols", "project_memory", "inspect_project"),
            _stage(
                "navigate", "Locate definitions or references", "code_intelligence", "find_code"
            ),
            _stage("read", "Read the exact symbol", "read_symbol", "read_files"),
        ),
        suggested_call_budget=7,
    ),
    WorkflowDefinition(
        "explain_code",
        "Explain code",
        "Explain inspected implementation behavior.",
        1,
        ("explain code", "how does", "walk me through", "what does this function", "code flow"),
        ("architecture",),
        75,
        (
            _stage("memory", "Retrieve relevant code context", "project_memory", "inspect_project"),
            _stage("read", "Read relevant implementation", "read_symbol", "read_files"),
            _stage(
                "relationships",
                "Inspect definitions and references",
                "code_intelligence",
                required=False,
            ),
        ),
        suggested_call_budget=8,
    ),
    WorkflowDefinition(
        "diagnose_bug",
        "Diagnose bug",
        "Find an evidence-backed cause without unrequested edits.",
        1,
        ("diagnose", "root cause", "why is", "bug", "error"),
        ("fix", "implement"),
        72,
        (
            _stage("changes", "Inspect recent changes", "changed_context", required=False),
            _stage(
                "memory", "Retrieve relevant project context", "project_memory", "inspect_project"
            ),
            _stage("navigate", "Inspect affected symbols", "code_intelligence", "read_symbol"),
            _stage(
                "reproduce", "Reproduce with a detected check", "run_project_checks", required=False
            ),
        ),
        suggested_call_budget=10,
    ),
    WorkflowDefinition(
        "fix_failing_test",
        "Fix failing test",
        "Reproduce, fix, and rerun a failing test.",
        1,
        ("fix failing test", "test failing", "failing test", "pytest failure", "test error"),
        (),
        100,
        (
            _stage("reproduce", "Reproduce the failing check", "run_project_checks"),
            _stage(
                "navigate",
                "Locate the failing implementation",
                "code_intelligence",
                "project_memory",
            ),
            _stage("read", "Read the affected symbol", "read_symbol", "read_files"),
            _stage("patch", "Apply the focused fix", "apply_patch"),
            _stage("verify", "Rerun the failing check", "run_project_checks"),
        ),
        WorkflowCompletionRule(True, True),
        14,
    ),
    WorkflowDefinition(
        "implement_feature",
        "Implement feature",
        "Implement and verify a project feature.",
        1,
        ("implement", "add feature", "build feature", "create feature", "develop"),
        ("test", "documentation"),
        88,
        (
            _stage("memory", "Retrieve project conventions", "project_memory", "inspect_project"),
            _stage("navigate", "Locate integration points", "code_intelligence", "read_symbol"),
            _stage(
                "dependencies",
                "Inspect relevant dependencies",
                "dependency_context",
                required=False,
            ),
            _stage("patch", "Apply the implementation", "apply_patch"),
            _stage("verify", "Run project checks", "run_project_checks"),
            _stage("review", "Review resulting changes", "git_inspect", required=False),
        ),
        WorkflowCompletionRule(True, True),
        15,
    ),
    WorkflowDefinition(
        "safe_refactor",
        "Safe refactor",
        "Refactor while preserving verified behavior.",
        1,
        ("refactor", "restructure", "clean up code", "rename across"),
        (),
        92,
        (
            _stage("baseline", "Inspect the current change baseline", "git_inspect"),
            _stage("memory", "Retrieve affected architecture", "project_memory", "inspect_project"),
            _stage(
                "navigate", "Inspect definitions and references", "code_intelligence", "read_symbol"
            ),
            _stage("patch", "Apply the refactor", "apply_patch"),
            _stage("verify", "Run behavior checks", "run_project_checks"),
            _stage("review", "Review the final diff", "git_inspect"),
        ),
        WorkflowCompletionRule(True, True),
        15,
    ),
    WorkflowDefinition(
        "create_or_update_tests",
        "Create or update tests",
        "Add focused tests and execute them.",
        1,
        ("add tests", "write tests", "update tests", "test coverage", "create test"),
        (),
        90,
        (
            _stage("navigate", "Inspect tested behavior", "code_intelligence", "project_memory"),
            _stage("read", "Read implementation and test patterns", "read_symbol", "read_files"),
            _stage("patch", "Apply test changes", "apply_patch"),
            _stage("verify", "Run focused tests", "run_project_checks"),
        ),
        WorkflowCompletionRule(True, True),
        12,
    ),
    WorkflowDefinition(
        "review_changes",
        "Review changes",
        "Review current changes without modifying files.",
        1,
        ("review changes", "review my changes", "code review", "review diff", "inspect diff"),
        ("fix",),
        95,
        (
            _stage("diff", "Inspect the current diff", "git_inspect"),
            _stage("context", "Retrieve changed context", "changed_context", "git_inspect"),
            _stage("navigate", "Inspect affected symbols", "code_intelligence", "read_symbol"),
            _stage("checks", "Run relevant checks", "run_project_checks", required=False),
        ),
        suggested_call_budget=10,
    ),
    WorkflowDefinition(
        "dependency_upgrade",
        "Dependency upgrade",
        "Research and safely upgrade a dependency.",
        1,
        ("upgrade dependency", "update dependency", "bump version", "dependency migration"),
        (),
        96,
        (
            _stage(
                "dependencies",
                "Inspect current dependency facts",
                "dependency_context",
                "inspect_project",
                "read_files",
            ),
            _stage("search", "Find official migration guidance", "web_search"),
            _stage("sources", "Read official release guidance", "read_web_pages"),
            _stage("patch", "Apply dependency changes", "apply_patch"),
            _stage("verify", "Run project checks", "run_project_checks"),
        ),
        WorkflowCompletionRule(True, True, True),
        16,
    ),
    WorkflowDefinition(
        "configuration_change",
        "Configuration change",
        "Change project configuration and validate it.",
        1,
        (
            "change config",
            "configuration change",
            "configure",
            "update settings",
            "environment setting",
        ),
        (),
        82,
        (
            _stage("memory", "Retrieve configuration context", "project_memory", "inspect_project"),
            _stage("inspect", "Inspect configuration usage", "find_code", "read_files"),
            _stage("patch", "Apply configuration changes", "apply_patch"),
            _stage("verify", "Validate project configuration", "run_project_checks"),
        ),
        WorkflowCompletionRule(True, True),
        12,
    ),
    WorkflowDefinition(
        "documentation_update",
        "Documentation update",
        "Update documentation from inspected behavior.",
        1,
        ("update docs", "write documentation", "readme", "document this", "documentation"),
        (),
        86,
        (
            _stage(
                "memory", "Retrieve documented architecture", "project_memory", "inspect_project"
            ),
            _stage("read", "Read implementation and existing docs", "read_files", "read_symbol"),
            _stage("patch", "Apply documentation changes", "apply_patch"),
            _stage("verify", "Run documentation checks", "run_project_checks", required=False),
        ),
        WorkflowCompletionRule(True),
        10,
    ),
    WorkflowDefinition(
        "api_integration",
        "API integration",
        "Integrate an external API from official contracts.",
        1,
        (
            "integrate api",
            "api integration",
            "connect to api",
            "sdk integration",
            "external service",
        ),
        (),
        91,
        (
            _stage(
                "dependencies",
                "Inspect integration dependencies",
                "dependency_context",
                "inspect_project",
            ),
            _stage("search", "Find official API documentation", "web_search"),
            _stage("sources", "Read the official API contract", "read_web_pages"),
            _stage(
                "navigate", "Locate local integration points", "code_intelligence", "project_memory"
            ),
            _stage("patch", "Apply the integration", "apply_patch"),
            _stage("verify", "Run integration checks", "run_project_checks"),
        ),
        WorkflowCompletionRule(True, True, True),
        18,
    ),
    WorkflowDefinition(
        "build_failure",
        "Build failure",
        "Reproduce and resolve a project build failure.",
        1,
        ("build failure", "build failed", "compile error", "cannot build", "build error"),
        (),
        98,
        (
            _stage("reproduce", "Reproduce the build failure", "run_project_checks"),
            _stage("inspect", "Inspect project build configuration", "inspect_project"),
            _stage("read", "Locate the failure source", "search_text", "find_code", "read_files"),
            _stage("patch", "Apply a focused build fix", "apply_patch", required=False),
            _stage("verify", "Rerun the build check", "run_project_checks"),
        ),
        WorkflowCompletionRule(False, True),
        14,
    ),
    WorkflowDefinition(
        "performance_investigation",
        "Performance investigation",
        "Measure and investigate a performance concern.",
        1,
        ("performance", "slow", "benchmark", "optimize", "latency"),
        (),
        84,
        (
            _stage(
                "memory",
                "Retrieve performance-sensitive context",
                "project_memory",
                "inspect_project",
            ),
            _stage(
                "navigate", "Inspect relevant implementation", "code_intelligence", "read_symbol"
            ),
            _stage("baseline", "Measure the current behavior", "run_powershell"),
            _stage("patch", "Apply an optimization", "apply_patch", required=False),
            _stage("measure", "Repeat the measurement", "run_powershell"),
        ),
        WorkflowCompletionRule(require_measurements=True),
        14,
    ),
    WorkflowDefinition(
        "security_review",
        "Security review",
        "Review code and dependencies for evidence-backed risks.",
        1,
        ("security review", "security audit", "vulnerability", "secure", "threat"),
        ("fix",),
        93,
        (
            _stage("diff", "Inspect current changes", "git_inspect"),
            _stage(
                "memory",
                "Retrieve security-relevant architecture",
                "project_memory",
                "inspect_project",
            ),
            _stage("search", "Find risky code patterns", "find_code", "search_text"),
            _stage(
                "dependencies",
                "Inspect dependency facts",
                "dependency_context",
                "inspect_project",
            ),
            _stage(
                "advisories",
                "Read official security advisories",
                "web_search",
                "read_web_pages",
                required=False,
            ),
        ),
        suggested_call_budget=14,
    ),
    WorkflowDefinition(
        "web_research",
        "Web research",
        "Research current information from primary sources.",
        1,
        ("search web", "research", "current", "latest", "online", "news"),
        ("compare", "dependency"),
        76,
        (
            _stage("search", "Find strong current sources", "web_search"),
            _stage("sources", "Read the strongest primary sources", "read_web_pages"),
        ),
        WorkflowCompletionRule(require_sources=True),
        10,
    ),
    WorkflowDefinition(
        "technology_comparison",
        "Technology comparison",
        "Compare technologies against project constraints.",
        1,
        ("compare", "versus", " vs ", "which technology", "choose between"),
        (),
        87,
        (
            _stage(
                "constraints",
                "Inspect project constraints",
                "dependency_context",
                "inspect_project",
            ),
            _stage("search", "Find current primary sources", "web_search"),
            _stage("sources", "Read the strongest sources", "read_web_pages"),
        ),
        WorkflowCompletionRule(require_sources=True),
        12,
    ),
    WorkflowDefinition(
        "windows_troubleshooting",
        "Windows troubleshooting",
        "Inspect and verify a Windows or terminal issue.",
        1,
        ("powershell", "windows", "terminal issue", "service not running", "process", "port"),
        (),
        85,
        (
            _stage("inspect", "Inspect local project state", "inspect_project", "list_directory"),
            _stage("diagnose", "Run an approved diagnostic command", "run_powershell"),
            _stage("verify", "Verify the resulting state", "run_powershell"),
        ),
        suggested_call_budget=12,
    ),
    WorkflowDefinition(
        "release_readiness",
        "Release readiness",
        "Assess whether the project is ready to release.",
        1,
        ("release readiness", "ready to release", "pre release", "ship", "release check"),
        (),
        94,
        (
            _stage("git", "Inspect repository state", "git_inspect"),
            _stage("changes", "Review changed project context", "changed_context", "git_inspect"),
            _stage("checks", "Run the complete quality gate", "run_project_checks"),
            _stage(
                "dependencies",
                "Inspect release dependencies",
                "dependency_context",
                "inspect_project",
            ),
            _stage("docs", "Inspect release documentation", "read_files", required=False),
        ),
        WorkflowCompletionRule(require_successful_check=True),
        14,
    ),
)


GENERAL_WORKFLOW = WorkflowDefinition(
    "general_assistance",
    "General assistance",
    "Use adaptive tool routing for an uncategorized request.",
    1,
    (),
    (),
    0,
    (),
    suggested_call_budget=20,
)


class WorkflowCatalog:
    """Validate, search, and retrieve built-in workflow definitions."""

    def __init__(self, definitions: tuple[WorkflowDefinition, ...] = WORKFLOWS) -> None:
        """Create a validated immutable workflow catalog."""
        values = (*definitions, GENERAL_WORKFLOW)
        if len({item.workflow_id for item in values}) != len(values):
            raise ValueError("Workflow IDs must be unique")
        if any(len(stage.tools) > 7 for item in values for stage in item.stages):
            raise ValueError("Workflow stages must fit the eight-schema tool limit")
        self._items = {item.workflow_id: item for item in values}

    def get(self, workflow_id: str) -> WorkflowDefinition:
        """Return one workflow or raise a user-facing validation error."""
        try:
            return self._items[workflow_id]
        except KeyError as exc:
            raise ToolExecutionError(f"Unknown workflow: {workflow_id}") from exc

    def list(self, query: str = "") -> tuple[WorkflowDefinition, ...]:
        """Return all or text-matching workflows in stable title order."""
        folded = query.strip().casefold()
        values = tuple(sorted(self._items.values(), key=lambda item: item.title.casefold()))
        if not folded:
            return values
        return tuple(
            item
            for item in values
            if folded in f"{item.workflow_id} {item.title} {item.description}".casefold()
            or any(folded in trigger for trigger in item.triggers)
        )


class WorkflowSelector:
    """Select one workflow without a model call."""

    def __init__(self, catalog: WorkflowCatalog, confidence_minimum: float = 0.60) -> None:
        """Configure deterministic selection and its safe fallback threshold."""
        if not 0.0 <= confidence_minimum <= 1.0:
            raise ValueError("Workflow confidence must be between 0 and 1")
        self._catalog = catalog
        self._minimum = confidence_minimum

    def select(
        self,
        prompt: str,
        explicit: str | None = None,
        *,
        source: Literal["explicit", "pending"] = "explicit",
    ) -> WorkflowSelection:
        """Select an explicit workflow or score the sanitized request."""
        if explicit:
            definition = self._catalog.get(explicit)
            return WorkflowSelection(definition.workflow_id, 1.0, source, ("explicit override",))
        folded = " ".join(prompt.casefold().split())
        tokens = set(re.findall(r"[a-z0-9]+", folded))
        ranked: list[tuple[float, WorkflowDefinition, tuple[str, ...]]] = []
        for definition in WORKFLOWS:
            if any(value in folded for value in definition.negative_triggers):
                continue
            signals = tuple(value for value in definition.triggers if value in folded)
            token_hits = sum(
                bool(set(re.findall(r"[a-z0-9]+", value)) & tokens) for value in definition.triggers
            )
            if not signals and token_hits < 2:
                continue
            raw = len(signals) * 4 + token_hits + definition.priority / 100
            confidence = min(1.0, 0.42 + len(signals) * 0.28 + min(token_hits, 3) * 0.08)
            ranked.append((raw, definition, signals or tuple(sorted(tokens)[:3])))
        ranked.sort(key=lambda value: (-value[0], -value[1].priority, value[1].workflow_id))
        if not ranked or ranked[0][0] == (ranked[1][0] if len(ranked) > 1 else -1):
            return WorkflowSelection("general_assistance", 0.0, "fallback")
        _, definition, signals = ranked[0]
        confidence = min(1.0, 0.42 + len(signals) * 0.28)
        if confidence < self._minimum:
            return WorkflowSelection("general_assistance", confidence, "fallback", signals)
        return WorkflowSelection(definition.workflow_id, confidence, "automatic", signals[:10])


class WorkflowCoordinator:
    """Own request workflow state and enforce stage-level tool eligibility."""

    def __init__(self, session: Session, catalog: WorkflowCatalog, max_attempts: int = 2) -> None:
        """Bind deterministic workflow transitions to one session."""
        if not 1 <= max_attempts <= 5:
            raise ValueError("Workflow stage attempts must be between 1 and 5")
        self._session = session
        self._catalog = catalog
        self._max_attempts = max_attempts
        self._run: WorkflowRun | None = None

    @property
    def run(self) -> WorkflowRun | None:
        """Return the active request workflow run."""
        return self._run

    @property
    def definition(self) -> WorkflowDefinition | None:
        """Return the selected definition when a workflow is active."""
        return self._catalog.get(self._run.workflow_id) if self._run is not None else None

    def clear(self) -> None:
        """Disable workflow enforcement for the next ordinary routed request."""
        self._run = None

    def start(self, request_number: int, selection: WorkflowSelection) -> WorkflowRun:
        """Create and persist a workflow run and its visible plan projection."""
        definition = self._catalog.get(selection.workflow_id)
        stages = tuple(
            WorkflowStageRun(stage.stage_id, stage.description) for stage in definition.stages
        )
        current = stages[0].stage_id if stages else ""
        if stages:
            stages = (replace(stages[0], status="in_progress"), *stages[1:])
        status: Literal["active", "completed", "blocked"] = "active" if stages else "completed"
        run = WorkflowRun(
            request_number,
            definition.workflow_id,
            definition.version,
            selection.source,
            selection.confidence,
            selection.matched_signals,
            stages,
            status,
            current,
        )
        self._session.workflows.append(run)
        if stages:
            self._session.plans.append(
                TaskPlan(
                    request_number,
                    definition.title,
                    tuple(
                        TaskStep(
                            index,
                            stage.description,
                            "in_progress" if index == 1 else "pending",
                            requires_verification=_verification_stage(stage),
                        )
                        for index, stage in enumerate(definition.stages, 1)
                    ),
                )
            )
        self._run = run
        return run

    def allowed_tools(self) -> tuple[str, ...]:
        """Return tools permitted for the current stage."""
        if self._run is None or self._run.status != "active":
            return ()
        definition = self._catalog.get(self._run.workflow_id)
        stage = next(
            item for item in definition.stages if item.stage_id == self._run.current_stage_id
        )
        return stage.tools

    def all_tools(self) -> tuple[str, ...]:
        """Return the deduplicated allowlist for the entire workflow."""
        definition = self.definition
        if definition is None:
            return ()
        return tuple(dict.fromkeys(tool for stage in definition.stages for tool in stage.tools))

    def instruction(self) -> str:
        """Render compact observable workflow context for the provider."""
        if self._run is None or self._run.workflow_id == "general_assistance":
            return ""
        stage = next(
            (item for item in self._run.stages if item.stage_id == self._run.current_stage_id), None
        )
        if stage is None:
            return f"Workflow {self._run.workflow_id} is {self._run.status}."
        tools = ", ".join(self.allowed_tools())
        return (
            f"Workflow: {self._run.workflow_id}. Current stage: {stage.description}. "
            f"Allowed tools: {tools}. Complete observable required stages before the "
            "final answer."
        )

    def before_tool(self, name: str) -> str:
        """Return an empty string when a call is allowed, otherwise a correction."""
        if (
            self._run is None
            or self._run.workflow_id == "general_assistance"
            or name in {"discover_tools", "task_plan"}
        ):
            return ""
        if self._run.status != "active":
            return f"Workflow is {self._run.status}; no further workflow tool can run."
        if name in self.allowed_tools():
            return ""
        definition = self._catalog.get(self._run.workflow_id)
        current_index = next(
            index
            for index, stage in enumerate(definition.stages)
            if stage.stage_id == self._run.current_stage_id
        )
        target_index = next(
            (
                index
                for index, stage in enumerate(
                    definition.stages[current_index + 1 :], current_index + 1
                )
                if name in stage.tools
            ),
            None,
        )
        if target_index is not None and all(
            not stage.required for stage in definition.stages[current_index:target_index]
        ):
            stages = list(self._run.stages)
            for index in range(current_index, target_index):
                stages[index] = replace(stages[index], status="skipped", result="Not required")
            stages[target_index] = replace(stages[target_index], status="in_progress")
            self._replace(
                replace(
                    self._run,
                    stages=tuple(stages),
                    current_stage_id=stages[target_index].stage_id,
                )
            )
            return ""
        return (
            f"Tool {name} is outside the current workflow stage. "
            f"Use one of: {', '.join(self.allowed_tools())}."
        )

    def after_tool(self, name: str, *, is_error: bool, summary: str) -> WorkflowRun | None:
        """Apply a tool outcome to the current stage and advance when possible."""
        if (
            self._run is None
            or self._run.workflow_id == "general_assistance"
            or name not in self.allowed_tools()
        ):
            return self._run
        index = next(
            i
            for i, item in enumerate(self._run.stages)
            if item.stage_id == self._run.current_stage_id
        )
        stages = list(self._run.stages)
        current = stages[index]
        attempts = current.attempts + 1
        if is_error and attempts < self._max_attempts:
            stages[index] = replace(current, attempts=attempts, result=summary[:500])
            return self._replace(replace(self._run, stages=tuple(stages)))
        if is_error:
            definition = self._catalog.get(self._run.workflow_id)
            required = definition.stages[index].required
            stages[index] = replace(
                current,
                status="blocked" if required else "skipped",
                attempts=attempts,
                result=summary[:500],
            )
            if required:
                return self._replace(replace(self._run, stages=tuple(stages), status="blocked"))
        else:
            stages[index] = replace(
                current, status="completed", attempts=attempts, result=summary[:500]
            )
        next_index = index + 1
        if next_index >= len(stages):
            return self._replace(
                replace(self._run, stages=tuple(stages), status="completed", current_stage_id="")
            )
        stages[next_index] = replace(stages[next_index], status="in_progress")
        return self._replace(
            replace(self._run, stages=tuple(stages), current_stage_id=stages[next_index].stage_id)
        )

    def completion_issues(
        self, *, changed: bool, successful_check: bool, sources: bool, measurements: int
    ) -> tuple[str, ...]:
        """Return unmet stage and evidence requirements."""
        if self._run is None or self._run.workflow_id == "general_assistance":
            return ()
        definition = self._catalog.get(self._run.workflow_id)
        issues = [
            f"Required workflow stage incomplete: {stage.description}"
            for stage, state in zip(definition.stages, self._run.stages, strict=True)
            if stage.required and state.status != "completed"
        ]
        rule = definition.completion
        if rule.require_changed_files and not changed:
            issues.append("No approved file change was recorded")
        if rule.require_successful_check and not successful_check:
            issues.append("No successful verification check was recorded")
        if rule.require_sources and not sources:
            issues.append("No successfully read source was recorded")
        if rule.require_measurements and measurements < 2:
            issues.append("Comparable before-and-after measurements were not recorded")
        return tuple(issues)

    def _replace(self, run: WorkflowRun) -> WorkflowRun:
        value = replace(run, updated_at=datetime.now(UTC).isoformat())
        old = self._run
        if old is None:
            raise RuntimeError("Workflow was not started")
        self._session.workflows[self._session.workflows.index(old)] = value
        self._sync_plan(value)
        self._run = value
        return value

    def _sync_plan(self, run: WorkflowRun) -> None:
        plan = next(
            (item for item in self._session.plans if item.request_number == run.request_number),
            None,
        )
        if plan is None:
            return
        mapped = {"skipped": "completed", "blocked": "blocked"}
        steps = tuple(
            replace(
                step,
                status=cast(
                    Literal["pending", "in_progress", "completed", "blocked"],
                    mapped.get(state.status, state.status),
                ),
                result=state.result,
            )
            for step, state in zip(plan.steps, run.stages, strict=True)
        )
        status: Literal["active", "completed", "blocked"] = (
            "blocked"
            if run.status == "blocked"
            else "completed"
            if run.status == "completed"
            else "active"
        )
        self._session.plans[self._session.plans.index(plan)] = replace(
            plan, steps=steps, status=status, updated_at=datetime.now(UTC).isoformat()
        )


def _verification_stage(stage: WorkflowStageDefinition) -> bool:
    return bool(
        set(stage.tools) & {"run_project_checks", "git_inspect", "read_web_pages", "run_powershell"}
    )
