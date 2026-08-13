"""Tests for deterministic situation-based workflow orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from local_harness.application.agent import AgentService
from local_harness.application.ports import Tool
from local_harness.application.tool_registry import ToolRegistry
from local_harness.application.tool_routing import RequestToolRouter
from local_harness.application.workflows import (
    GENERAL_WORKFLOW,
    WORKFLOWS,
    WorkflowCatalog,
    WorkflowCoordinator,
    WorkflowSelector,
)
from local_harness.domain.errors import ToolExecutionError
from local_harness.domain.models import Message, Session, ToolCall, ToolDefinition, ToolResult
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.json_sessions import JsonSessionRepository


@dataclass
class _Model:
    replies: list[Message]
    schemas: list[tuple[str, ...]] = field(default_factory=list)

    def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> Message:
        self.schemas.append(tuple(item.name for item in tools))
        return self.replies.pop(0)


class _Tool:
    def __init__(self, name: str) -> None:
        self._definition = ToolDefinition(
            name,
            f"Use {name}",
            {"type": "object", "properties": {}, "additionalProperties": False},
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        return ToolResult('{"version":1,"summary":"ok","items":[]}')


class _Sessions:
    def save(self, session: Session) -> None:
        return None

    def load(self, session_id: str) -> Session:
        raise AssertionError

    def list_sessions(self) -> list[Session]:
        return []


def test_catalog_contains_twenty_valid_built_in_workflows() -> None:
    """The built-in catalog is unique, bounded, and includes the safe fallback."""
    catalog = WorkflowCatalog()

    assert len(WORKFLOWS) == 20
    assert len({item.workflow_id for item in WORKFLOWS}) == 20
    assert catalog.get(GENERAL_WORKFLOW.workflow_id) == GENERAL_WORKFLOW
    assert all(stage.tools and len(stage.tools) <= 7 for item in WORKFLOWS for stage in item.stages)
    with pytest.raises(ToolExecutionError, match="Unknown workflow"):
        catalog.get("missing")


def test_workflow_configuration_validation_edges() -> None:
    """Invalid catalogs and coordinator limits fail before request execution."""
    with pytest.raises(ValueError, match="unique"):
        WorkflowCatalog((WORKFLOWS[0], WORKFLOWS[0]))
    oversized_stage = replace(WORKFLOWS[0].stages[0], tools=tuple(f"tool_{i}" for i in range(8)))
    with pytest.raises(ValueError, match="eight-schema"):
        WorkflowCatalog((replace(WORKFLOWS[0], stages=(oversized_stage,)),))
    with pytest.raises(ValueError, match="confidence"):
        WorkflowSelector(WorkflowCatalog(), 1.1)
    with pytest.raises(ValueError, match="attempts"):
        WorkflowCoordinator(Session("0" * 32, ".", "model"), WorkflowCatalog(), 0)


@pytest.mark.parametrize(
    ("prompt", "workflow_id"),
    [
        ("Fix this failing pytest test", "fix_failing_test"),
        ("Review my changes and diff", "review_changes"),
        ("Upgrade dependency safely", "dependency_upgrade"),
        ("Search web for current Python guidance", "web_research"),
        ("Check whether we are ready to release", "release_readiness"),
        ("Implement a new authentication feature", "implement_feature"),
        ("Refactor this service safely", "safe_refactor"),
        ("Why is this bug happening? diagnose root cause", "diagnose_bug"),
        ("Add tests for the parser", "create_or_update_tests"),
        ("Build failed with a compile error", "build_failure"),
    ],
)
def test_selector_routes_high_value_situations(prompt: str, workflow_id: str) -> None:
    """Representative requests select their expected workflow without a model call."""
    selection = WorkflowSelector(WorkflowCatalog()).select(prompt)

    assert selection.workflow_id == workflow_id
    assert selection.source == "automatic"


def test_selector_falls_back_and_honors_explicit_override() -> None:
    """Conversational ambiguity falls back while an exact override always wins."""
    selector = WorkflowSelector(WorkflowCatalog())

    fallback = selector.select("Hello, can you help me?")
    explicit = selector.select("anything", "security_review")

    assert fallback.workflow_id == "general_assistance"
    assert fallback.source == "fallback"
    assert explicit.workflow_id == "security_review"
    assert explicit.confidence == 1.0


def test_coordinator_advances_stages_and_synchronizes_plan() -> None:
    """Successful observable tools advance workflow and its plan projection."""
    session = Session("a" * 32, ".", "model")
    catalog = WorkflowCatalog()
    coordinator = WorkflowCoordinator(session, catalog)
    selection = WorkflowSelector(catalog).select("Locate where login is implemented")

    run = coordinator.start(1, selection)
    assert run.workflow_id == "locate_code"
    assert set(coordinator.allowed_tools()) == {"project_memory", "inspect_project"}

    coordinator.after_tool("project_memory", is_error=False, summary="Found symbols")
    assert set(coordinator.allowed_tools()) == {"code_intelligence", "find_code"}
    coordinator.after_tool("code_intelligence", is_error=False, summary="Located login")
    coordinator.after_tool("read_symbol", is_error=False, summary="Read login")

    assert coordinator.run is not None and coordinator.run.status == "completed"
    assert "completed" in coordinator.before_tool("read_symbol")
    assert session.plans[0].status == "completed"
    assert all(step.status == "completed" for step in session.plans[0].steps)


def test_coordinator_blocks_required_stage_after_two_failures() -> None:
    """A repeated required-stage failure blocks instead of looping forever."""
    session = Session("b" * 32, ".", "model")
    catalog = WorkflowCatalog()
    coordinator = WorkflowCoordinator(session, catalog, max_attempts=2)
    coordinator.start(
        1,
        WorkflowSelector(catalog).select("Fix this failing test"),
    )

    coordinator.after_tool("run_project_checks", is_error=True, summary="Failed once")
    assert coordinator.run is not None and coordinator.run.status == "active"
    coordinator.after_tool("run_project_checks", is_error=True, summary="Failed twice")

    blocked = session.workflows[-1]
    assert blocked.status == "blocked"
    assert session.plans[0].status == "blocked"


def test_completion_rules_report_missing_evidence() -> None:
    """Mutation workflows cannot claim completion without changes and checks."""
    session = Session("c" * 32, ".", "model")
    catalog = WorkflowCatalog()
    coordinator = WorkflowCoordinator(session, catalog)
    coordinator.start(1, WorkflowSelector(catalog).select("Implement a feature"))

    issues = coordinator.completion_issues(
        changed=False,
        successful_check=False,
        sources=False,
        measurements=0,
    )

    assert "No approved file change was recorded" in issues
    assert "No successful verification check was recorded" in issues


def test_workflow_source_measurement_and_inactive_guards() -> None:
    """Special completion claims require sources or comparable measurements."""
    session = Session("1" * 32, ".", "model")
    catalog = WorkflowCatalog()
    coordinator = WorkflowCoordinator(session, catalog)

    assert coordinator.allowed_tools() == ()
    assert coordinator.all_tools() == ()
    assert coordinator.instruction() == ""
    assert coordinator.after_tool("anything", is_error=False, summary="ignored") is None
    coordinator.start(1, WorkflowSelector(catalog).select("anything", "web_research"))
    source_issues = coordinator.completion_issues(
        changed=False, successful_check=False, sources=False, measurements=0
    )
    assert any("successfully read source" in issue for issue in source_issues)
    coordinator.clear()
    coordinator.start(2, WorkflowSelector(catalog).select("anything", "performance_investigation"))
    measurement_issues = coordinator.completion_issues(
        changed=False, successful_check=False, sources=False, measurements=1
    )
    assert any("before-and-after" in issue for issue in measurement_issues)


def test_out_of_stage_tool_is_rejected_and_missing_plan_is_safe() -> None:
    """Stage enforcement gives a compact correction and tolerates a missing projection."""
    session = Session("2" * 32, ".", "model")
    catalog = WorkflowCatalog()
    coordinator = WorkflowCoordinator(session, catalog)
    coordinator.start(1, WorkflowSelector(catalog).select("anything", "locate_code"))

    assert "outside the current workflow stage" in coordinator.before_tool("read_symbol")
    session.plans.clear()
    coordinator.after_tool("project_memory", is_error=False, summary="found")
    assert coordinator.run is not None and coordinator.run.current_stage_id == "navigate"


def test_optional_stage_failure_skips_and_advances() -> None:
    """An exhausted optional stage is skipped instead of blocking the workflow."""
    session = Session("3" * 32, ".", "model")
    catalog = WorkflowCatalog()
    coordinator = WorkflowCoordinator(session, catalog, max_attempts=1)
    coordinator.start(1, WorkflowSelector(catalog).select("anything", "diagnose_bug"))

    coordinator.after_tool("changed_context", is_error=True, summary="Git unavailable")

    assert coordinator.run is not None
    assert coordinator.run.stages[0].status == "skipped"
    assert coordinator.run.current_stage_id == "memory"


def test_schema_v7_round_trips_workflow_and_pending_override(tmp_path: Path) -> None:
    """Workflow history and a pending one-shot override survive session resume."""
    session = Session("d" * 32, str(tmp_path), "model")
    catalog = WorkflowCatalog()
    coordinator = WorkflowCoordinator(session, catalog)
    coordinator.start(1, WorkflowSelector(catalog).select("Review my changes"))
    session.pending_workflow_override = "release_readiness"
    repository = JsonSessionRepository(tmp_path, SecretRedactor())

    repository.save(session)
    loaded = repository.load(session.session_id)

    assert loaded.schema_version == 7
    assert loaded.workflows[0].workflow_id == "review_changes"
    assert loaded.pending_workflow_override == "release_readiness"


def test_optional_stage_can_be_skipped_for_a_later_allowed_tool() -> None:
    """Hybrid enforcement permits deterministic omission of an optional stage."""
    session = Session("e" * 32, ".", "model")
    catalog = WorkflowCatalog()
    coordinator = WorkflowCoordinator(session, catalog)
    coordinator.start(1, WorkflowSelector(catalog).select("Build failed with an error"))
    coordinator.after_tool("run_project_checks", is_error=False, summary="reproduced")
    coordinator.after_tool("inspect_project", is_error=False, summary="inspected")
    coordinator.after_tool("read_files", is_error=False, summary="read")

    assert coordinator.before_tool("run_project_checks") == ""
    coordinator.after_tool("run_project_checks", is_error=False, summary="build passed")

    assert coordinator.run is not None and coordinator.run.status == "completed"


def test_agent_executes_workflow_stages_without_selection_model_call() -> None:
    """The agent changes schemas by stage and completes one routed workflow end to end."""
    tools: list[Tool] = [
        _Tool(name)
        for name in (
            "project_memory",
            "inspect_project",
            "code_intelligence",
            "find_code",
            "read_symbol",
            "read_files",
        )
    ]
    base = ToolRegistry(tools)
    router = RequestToolRouter(base.tools, schema_limit=8, activation_limit=5)
    registry = base.with_tools([router])
    model = _Model(
        [
            Message(
                "assistant",
                tool_calls=(ToolCall("1", "project_memory", '{"step_summary":"Find login"}'),),
            ),
            Message(
                "assistant",
                tool_calls=(ToolCall("2", "code_intelligence", '{"step_summary":"Locate login"}'),),
            ),
            Message(
                "assistant",
                tool_calls=(ToolCall("3", "read_symbol", '{"step_summary":"Read login"}'),),
            ),
            Message(
                "assistant",
                "<step_summary>Located login</step_summary>\nLogin is implemented in `app.py`.",
            ),
        ]
    )
    session = Session("f" * 32, ".", "model")
    catalog = WorkflowCatalog()
    agent = AgentService(
        model_client=model,
        registry=registry,
        sessions=_Sessions(),
        session=session,
        system_prompt="system",
        max_turns=10,
        tool_router=router,
        workflow_catalog=catalog,
        workflow_selector=WorkflowSelector(catalog),
        workflow_mode="auto",
    )

    result = agent.submit("Locate where login is implemented")

    assert result == "Login is implemented in `app.py`."
    assert len(model.schemas) == 4
    assert "project_memory" in model.schemas[0]
    assert "code_intelligence" in model.schemas[1]
    assert "read_symbol" in model.schemas[2]
    assert session.workflows[-1].status == "completed"
