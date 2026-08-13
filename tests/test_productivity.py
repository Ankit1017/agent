"""Tests for analytics, maintenance, tagging, and plugin expansion."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from local_harness.application.agent import AgentService
from local_harness.application.session_services import SessionService, session_info
from local_harness.application.tool_registry import ToolRegistry
from local_harness.domain.errors import ConfigurationError, SessionError
from local_harness.domain.models import (
    ApprovalDecision,
    Message,
    ModelCompletion,
    ProgressEvent,
    Session,
    SessionSummary,
    TokenUsage,
    ToolDefinition,
    ToolResult,
)
from local_harness.domain.plugins import PluginContext
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.json_sessions import JsonSessionRepository
from local_harness.infrastructure.plugins import load_plugins
from local_harness.infrastructure.session_files import SessionFileService


@dataclass
class CompletionModel:
    """Return queued completions with provider usage."""

    replies: list[ModelCompletion]

    def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelCompletion:
        """Return the next completion."""
        return self.replies.pop(0)


@dataclass
class NoopTool:
    """Minimal closed-schema tool for agent and plugin tests."""

    name: str = "noop"

    @property
    def definition(self) -> ToolDefinition:
        """Return a valid closed schema."""
        return ToolDefinition(
            self.name,
            "A deterministic no-op tool.",
            {"type": "object", "properties": {}, "additionalProperties": False},
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Return a fixed result."""
        return ToolResult("ok")


@dataclass
class MemoryRepository:
    """Persist sessions in memory for application tests."""

    saves: int = 0

    def save(self, session: Session) -> None:
        """Count a save."""
        self.saves += 1

    def load(self, session_id: str) -> Session:
        """Reject unsupported loading."""
        raise AssertionError("not used")

    def list_sessions(self) -> list[Session]:
        """Return no sessions."""
        return []


class Clock:
    """Return deterministic model timings."""

    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        """Return the next time."""
        return next(self._values)


def test_agent_sanitizes_summarizes_tracks_usage_and_warns() -> None:
    """One request stores only redacted input and refreshes analytics."""
    model = CompletionModel(
        [
            ModelCompletion(
                Message(
                    role="assistant",
                    content="<step_summary>Finished safely</step_summary>\nDone",
                ),
                TokenUsage(8, 4, "provider"),
            )
        ]
    )
    session = Session("a" * 32, "C:\\work", "model")
    agent = AgentService(
        model_client=model,
        registry=ToolRegistry([NoopTool()]),
        sessions=MemoryRepository(),
        session=session,
        system_prompt="system",
        max_turns=2,
        sanitizer=SecretRedactor().sanitize,
        token_budget=10,
        clock=Clock(1.0, 2.0),
    )

    assert agent.submit("token=very-secret-value") == "Done"
    assert session.messages[0].content == "token=[REDACTED]"
    assert session.summary is not None
    assert session.summary.generation == "deterministic"
    assert agent.token_usage == 12
    assert any(event.kind == "security_notice" for event in session.events)
    assert any(event.kind == "quota_warning" and event.target == "100%" for event in session.events)


def test_explicit_llm_summary_records_estimated_usage() -> None:
    """The explicit summary call replaces the deterministic overview and records usage."""
    model = CompletionModel(
        [ModelCompletion(Message(role="assistant", content="Completed two useful tasks."))]
    )
    session = Session(
        "b" * 32,
        "C:\\work",
        "model",
        summary=SessionSummary("old", "deterministic"),
        messages=[Message(role="user", content="work")],
    )
    agent = AgentService(
        model_client=model,
        registry=ToolRegistry([NoopTool()]),
        sessions=MemoryRepository(),
        session=session,
        system_prompt="system",
        max_turns=2,
        clock=Clock(1.0, 2.0),
    )

    assert agent.summarize_with_model() == "Completed two useful tasks."
    assert session.summary is not None and session.summary.generation == "llm"
    assert session.events[-1].kind == "summary_complete"
    assert session.events[-1].usage_source == "estimated"


class ApproveMaintenance:
    """Approve every test maintenance operation."""

    def request_maintenance(self, action: str, details: str) -> ApprovalDecision:
        """Return explicit approval."""
        return ApprovalDecision(True)


def _session_service(tmp_path: Path) -> tuple[SessionService, JsonSessionRepository]:
    redactor = SecretRedactor(("secret-value",))
    repository = JsonSessionRepository(tmp_path, redactor)
    files = SessionFileService(tmp_path, repository, redactor)
    return SessionService(repository, files, files, files, ApproveMaintenance()), repository


def test_tags_exports_archive_restore_and_integrity(tmp_path: Path) -> None:
    """Session productivity operations round-trip through protected storage."""
    service, repository = _session_service(tmp_path)
    session = Session(
        "c" * 32,
        str(tmp_path),
        "model",
        summary=SessionSummary("secret-value outcome", "deterministic"),
        messages=[Message(role="user", content="=cmd secret-value", request_number=1)],
        events=[ProgressEvent(1, 1, "model_complete", "Done", "final", "success")],
    )
    repository.save(session)

    assert service.add_tag(session, 1, "Bug") == "bug"
    with pytest.raises(SessionError, match="already"):
        service.add_tag(session, 1, "bug")
    assert service.filter_events(session, "tag:bug")[0].sequence == 1
    session.events.extend(
        [
            ProgressEvent(2, 2, "tool_error", "Failed", "tool", "error"),
            ProgressEvent(3, 3, "model_start", "Waiting", "model", "started"),
        ]
    )
    assert len(service.filter_events(session, "")) == 3
    assert len(service.filter_events(session, "model")) == 2
    assert len(service.filter_events(session, "tool")) == 1
    assert len(service.filter_events(session, "error")) == 1
    assert len(service.filter_events(session, "running")) == 1
    assert len(service.filter_events(session, "tagged")) == 1
    assert len(service.filter_events(session, "failed")) == 1
    assert "Tokens:" in session_info(session, 0)
    markdown = service.export(session, "md")
    csv_export = service.export(session, "csv")
    assert "secret-value" not in Path(markdown.path).read_text(encoding="utf-8")
    assert "record_type" in Path(csv_export.path).read_text(encoding="utf-8")

    archive = service.archive(session.session_id)
    assert archive.session_id == session.session_id
    assert repository.list_sessions() == []
    assert service.list_archives()[0].session_id == session.session_id
    assert service.restore(session.session_id).session_id == session.session_id

    bad = tmp_path / ".harness" / "sessions" / f"{'d' * 32}.json"
    bad.write_text("not-json", encoding="utf-8")
    finding = service.scan()[0]
    quarantined = Path(service.quarantine(finding.check_id))
    assert quarantined.exists()
    assert not bad.exists()

    assert service.remove_tag(session, 1, "bug") == "bug"
    with pytest.raises(SessionError):
        service.remove_tag(session, 1, "missing")
    with pytest.raises(SessionError):
        service.add_tag(session, 999, "idea")
    with pytest.raises(SessionError):
        service.add_tag(session, 1, "bad tag")


class FakeEntryPoint:
    """Track whether plugin discovery imports an entry point."""

    name = "sample"

    def __init__(self) -> None:
        self.loaded = False

    def load(self) -> object:
        """Return a plugin factory and record the import boundary."""
        self.loaded = True
        return lambda context: [NoopTool("plugin_noop")]


def test_plugins_require_allowlisting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery remains inert while enabled plugins load validated wrapped tools."""
    entry = FakeEntryPoint()
    monkeypatch.setattr(
        "local_harness.infrastructure.plugins.metadata.entry_points",
        lambda **kwargs: [entry],
    )
    context = PluginContext(1, "C:\\work", 100)

    tools, statuses = load_plugins((), context, SecretRedactor(), set())
    assert tools == [] and statuses[0].state == "discovered"
    assert not entry.loaded

    tools, statuses = load_plugins(("sample",), context, SecretRedactor(), set())
    assert statuses[0].tools == ("plugin_noop",)
    assert tools[0].execute({}).content == "ok"
    assert entry.loaded

    with pytest.raises(ConfigurationError, match="not installed"):
        load_plugins(("missing",), context, SecretRedactor(), set())
