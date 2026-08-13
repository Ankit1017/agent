"""Tests for request routing, task plans, evidence, and curated coding tools."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from local_harness.application.evidence import (
    append_verification,
    build_completion_evidence,
    enforce_evidence_consistency,
)
from local_harness.application.task_plans import TaskPlanService
from local_harness.application.tool_routing import RequestToolRouter
from local_harness.domain.errors import ToolExecutionError
from local_harness.domain.models import (
    Message,
    ProgressEvent,
    Session,
    TaskPlan,
    TaskStep,
    ToolDefinition,
    ToolResult,
)
from local_harness.guardrails.path_policy import WorkspacePathPolicy
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.code_intelligence import CodeIntelligenceTool
from local_harness.infrastructure.git_tools import GitInspectTool
from local_harness.infrastructure.plan_tool import TaskPlanTool


class _Tool:
    def __init__(self, name: str, description: str = "coding project tool") -> None:
        self._definition = ToolDefinition(
            name,
            description,
            {"type": "object", "properties": {}, "additionalProperties": False},
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def execute(self, arguments: object) -> ToolResult:
        return ToolResult("ok")


class _Sessions:
    def __init__(self) -> None:
        self.saves = 0

    def save(self, session: Session) -> None:
        self.saves += 1

    def load(self, session_id: str) -> Session:
        raise AssertionError

    def list_sessions(self) -> list[Session]:
        return []


class _Finder:
    def find(
        self,
        query: str,
        path: str,
        kind: str,
        languages: list[str],
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, object]], bool, str | None]:
        if query == "explode":
            raise RuntimeError("index unavailable")
        return ([{"path": "app.py", "line": 3, "name": query, "kind": kind}], False, None)


def test_router_limits_schemas_and_discovers_tools() -> None:
    """A coding request starts bounded and discovery activates omitted capabilities."""
    tools = [
        _Tool("inspect_project"),
        _Tool("find_code"),
        _Tool("read_files"),
        _Tool("task_plan"),
        _Tool("git_inspect", "review git changes and history"),
        _Tool("apply_patch", "edit code files"),
        _Tool("run_project_checks", "run tests lint type checks"),
        _Tool("web_search", "search current web sources"),
    ]
    router = RequestToolRouter(tools, schema_limit=6, activation_limit=3)

    selection = router.start("Fix this Python bug")
    matches = router.discover("review git changes")

    assert selection.profile == "coding"
    assert len(selection.names) <= 6
    assert selection.catalog_schema_chars >= selection.selected_schema_chars
    assert matches[0].name == "git_inspect"
    assert router.is_active("git_inspect")

    review = router.start("Review my changes")
    assert review.profile == "coding"
    assert "git_inspect" in review.names


@pytest.mark.parametrize(
    ("prompt", "profile"),
    [
        ("Search current official news", "web"),
        ("Run a PowerShell service command", "system"),
        ("Hello there", "general"),
    ],
)
def test_router_profiles_and_discovery_envelope(prompt: str, profile: str) -> None:
    """Profile selection and model-facing discovery stay deterministic."""
    router = RequestToolRouter(
        [_Tool("inspect_project"), _Tool("web_search", "search current web news")],
        schema_limit=2,
        activation_limit=1,
    )
    assert router.start(prompt).profile == profile
    result = router.execute({"query": "web search"})
    assert not result.is_error
    assert json.loads(result.content)["metadata"]["active_tools"]
    assert router.catalog("web")
    assert router.execute({"query": ""}).is_error


def test_router_rejects_invalid_limits() -> None:
    """Schema and activation limits cannot create an invalid router."""
    with pytest.raises(ValueError):
        RequestToolRouter([_Tool("inspect_project")], schema_limit=0)
    with pytest.raises(ValueError):
        RequestToolRouter([_Tool("inspect_project")], schema_limit=2, activation_limit=3)


def test_task_plan_and_evidence_are_observable() -> None:
    """Plans enforce transitions and evidence renders recorded checks only."""
    session = Session("a" * 32, ".", "model")
    service = TaskPlanService(session)
    service.create(
        1,
        "Fix tests",
        [
            {"description": "Patch code"},
            {"description": "Run tests", "requires_verification": True},
        ],
    )
    service.update_step(1, 1, "completed", "Changed app.py")
    service.update_step(1, 2, "completed", "pytest passed")
    plan = service.complete(1)
    messages = [
        Message(
            "tool",
            json.dumps({"items": [{"path": "app.py"}]}),
            name="apply_patch",
            request_number=1,
        ),
        Message(
            "tool",
            json.dumps({"items": [{"profile": "tests", "status": "completed"}]}),
            name="run_project_checks",
            request_number=1,
        ),
    ]
    events = [
        ProgressEvent(
            1, 1, "tool_complete", "Tests passed", "run_project_checks", "success", request_number=1
        )
    ]

    evidence = build_completion_evidence(messages, events, [plan], 1)
    answer = append_verification("Fixed.", evidence)

    assert plan.status == "completed"
    assert evidence.changed_files == ("app.py",)
    assert evidence.checks == ("tests: completed",)
    assert "## Verification" in answer


def test_evidence_collects_sources_and_limitations() -> None:
    """Web sources and failed observable operations survive in bounded evidence."""
    messages = [
        Message(
            "tool",
            json.dumps({"items": [{"url": "https://example.com/source"}]}),
            name="web_search",
            request_number=2,
        ),
        Message(
            "tool",
            json.dumps({"items": [{"final_url": "https://example.com/page"}]}),
            name="read_web_pages",
            request_number=2,
        ),
        Message("tool", "not-json", name="read_web_pages", request_number=2),
    ]
    events = [
        ProgressEvent(
            1, 1, "tool_error", "Fetch failed", "read_web_pages", "error", request_number=2
        )
    ]
    plan = TaskPlan(2, "Research", (TaskStep(1, "Read", "blocked"),), "blocked")

    evidence = build_completion_evidence(messages, events, [plan], 2)

    assert evidence.sources == (
        "https://example.com/source",
        "https://example.com/page",
    )
    assert "Task plan remains blocked" in evidence.limitations
    assert append_verification("Answer", evidence) == "Answer"
    assert (
        enforce_evidence_consistency("All tests passed.", evidence)
        == "check success was not verified."
    )


def test_task_plan_tool_operations_and_verification_gate() -> None:
    """The model adapter persists valid operations and rejects unsupported transitions."""
    session = Session(
        "b" * 32,
        ".",
        "model",
        messages=[Message("user", "fix", request_number=1)],
    )
    sessions = _Sessions()
    tool = TaskPlanTool(session, sessions)
    created = tool.execute(
        {
            "operation": "create",
            "goal": "Fix tests",
            "steps": [
                {"description": "Run tests", "requires_verification": True},
            ],
        }
    )
    assert not created.is_error
    assert not tool.execute({"operation": "view"}).is_error
    assert not tool.execute(
        {
            "operation": "update_step",
            "step_id": 1,
            "status": "completed",
            "result": "pytest passed",
        }
    ).is_error
    assert tool.execute({"operation": "complete"}).is_error
    session.events.append(
        ProgressEvent(
            1,
            1,
            "tool_complete",
            "Tests passed",
            "run_project_checks",
            "success",
            request_number=1,
        )
    )
    assert not tool.execute({"operation": "complete"}).is_error
    assert tool.execute({"operation": "unknown"}).is_error
    assert sessions.saves == 4


def test_task_plan_service_rejects_invalid_transitions() -> None:
    """Plan invariants reject duplicates, concurrent work, and missing results."""
    session = Session("c" * 32, ".", "model")
    service = TaskPlanService(session)
    service.create(1, "Goal", [{"description": "One"}, {"description": "Two"}])
    with pytest.raises(ToolExecutionError):
        service.create(1, "Again", [{"description": "One"}])
    service.update_step(1, 1, "in_progress", "")
    with pytest.raises(ToolExecutionError):
        service.update_step(1, 2, "in_progress", "")
    with pytest.raises(ToolExecutionError):
        service.update_step(1, 99, "completed", "done")
    with pytest.raises(ToolExecutionError):
        service.complete(1)


def test_code_intelligence_fallback_and_diagnostics() -> None:
    """Navigation uses bounded syntax fallback and diagnostics report availability."""
    tool = CodeIntelligenceTool(
        cast(Any, _Finder()),
        SecretRedactor(),
        max_output_chars=10_000,
        python_command="pyright-langserver",
    )
    definition = tool.execute(
        {"operation": "definition", "query": "login", "path": ".", "language": "python"}
    )
    hover = tool.execute(
        {"operation": "hover", "query": "login", "path": ".", "language": "python"}
    )
    diagnostics = tool.execute({"operation": "diagnostics", "path": ".", "language": "python"})

    assert not definition.is_error and "tree-sitter-fallback" in definition.content
    assert not hover.is_error
    assert not diagnostics.is_error and "pyright-langserver" in diagnostics.content
    assert tool.execute(
        {"operation": "definition", "query": "", "path": ".", "language": "python"}
    ).is_error
    assert tool.execute(
        {"operation": "definition", "query": "explode", "path": ".", "language": "python"}
    ).is_error
    assert tool.execute(
        {"operation": "unknown", "query": "x", "path": ".", "language": "python"}
    ).is_error


def test_git_inspect_uses_read_only_operations(tmp_path: Path) -> None:
    """Git overview reports repository state without mutating the index."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "sample.py").write_text("print('ok')\n", encoding="utf-8")
    tool = GitInspectTool(WorkspacePathPolicy(tmp_path), SecretRedactor(), max_output_chars=10_000)

    result = tool.execute({"operation": "overview", "limit": 5})

    assert not result.is_error
    assert "sample.py" in result.content


def test_git_inspect_diff_history_and_blame(tmp_path: Path) -> None:
    """All curated Git operations remain read-only and validate arguments."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "sample.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "initial"], check=True)
    source.write_text("value = 2\n", encoding="utf-8")
    tool = GitInspectTool(WorkspacePathPolicy(tmp_path), SecretRedactor(), max_output_chars=20_000)

    assert not tool.execute({"operation": "diff", "paths": ["sample.py"]}).is_error
    assert not tool.execute({"operation": "history", "limit": 5}).is_error
    assert not tool.execute({"operation": "blame", "paths": ["sample.py"]}).is_error
    assert tool.execute({"operation": "blame", "paths": []}).is_error
    assert tool.execute({"operation": "diff", "base": "--unsafe"}).is_error
    assert tool.execute({"operation": "history", "limit": True}).is_error
