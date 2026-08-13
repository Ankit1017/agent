"""Edge and interface tests for productivity expansion."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from local_harness.application.agent import AgentService
from local_harness.application.session_services import SessionService
from local_harness.application.workflows import WORKFLOWS
from local_harness.bootstrap import Runtime
from local_harness.domain.errors import ConfigurationError, SessionError
from local_harness.domain.maintenance import PluginStatus
from local_harness.domain.models import (
    ApprovalDecision,
    ProgressEvent,
    Session,
    ToolDefinition,
    ToolResult,
)
from local_harness.domain.plugins import PluginContext
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.json_sessions import JsonSessionRepository
from local_harness.infrastructure.plugins import load_plugins
from local_harness.infrastructure.session_files import SessionFileService
from local_harness.interfaces.cli import _handle_plain_command
from local_harness.interfaces.commands import InterfaceCommand


class Approval:
    """Configurable maintenance confirmation fake."""

    def __init__(self, approved: bool = True) -> None:
        self.approved = approved

    def request_maintenance(self, action: str, details: str) -> ApprovalDecision:
        """Return the configured decision."""
        return ApprovalDecision(self.approved)


@dataclass
class CommandAgent:
    """Expose command-facing agent state without model calls."""

    session: Session
    token_usage: int = 25
    token_budget: int = 0
    max_turns: int = 20
    max_turns_source: str = "default"

    def configure_token_budget(self, value: int | None) -> int:
        """Set or clear the advisory budget."""
        if value is not None and value <= 0:
            raise ValueError("positive required")
        self.session.token_budget_override = value
        self.token_budget = value or 0
        return self.token_budget

    def summarize_with_model(self) -> str:
        """Return a deterministic command result."""
        return "LLM summary"

    def workflow_catalog(self, query: str = "") -> tuple[object, ...]:
        """Return the built-in workflow catalog, optionally filtered."""
        lowered = query.casefold()
        return tuple(
            workflow
            for workflow in WORKFLOWS
            if not lowered
            or lowered in workflow.workflow_id
            or lowered in workflow.title.casefold()
        )

    def workflow_status(self) -> None:
        """Return no completed workflow for the command edge test."""
        return None

    def configure_workflow(self, workflow_id: str | None) -> str | None:
        """Set the one-shot workflow override."""
        if workflow_id is not None and workflow_id not in {item.workflow_id for item in WORKFLOWS}:
            raise ValueError("unknown workflow")
        self.session.pending_workflow_override = workflow_id
        return workflow_id


class CommandRuntime:
    """Provide a real session service behind a minimal command runtime."""

    def __init__(self, tmp_path: Path) -> None:
        redactor = SecretRedactor()
        self.workspace = tmp_path
        self.settings = SimpleNamespace(session_token_budget=0)
        self.sessions = JsonSessionRepository(tmp_path, redactor)
        files = SessionFileService(tmp_path, self.sessions, redactor)
        self.session_service = SessionService(self.sessions, files, files, files, Approval())
        self.plugin_statuses = [PluginStatus("sample", "discovered")]

    def agent(self, session: Session) -> CommandAgent:
        """Return a command agent for a loaded session."""
        return CommandAgent(session)

    def new_session(self) -> Session:
        """Create a distinct saved session."""
        session = Session("f" * 32, str(self.workspace), "model")
        self.sessions.save(session)
        return session


def test_plain_productivity_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Plain mode routes analytics, tags, exports, archives, checks, and plugins."""
    runtime = CommandRuntime(tmp_path)
    session = Session(
        "a" * 32,
        str(tmp_path),
        "model",
        events=[ProgressEvent(1, 1, "model_complete", "Done", "final", "success")],
    )
    runtime.sessions.save(session)
    agent = CommandAgent(session)

    commands = [
        InterfaceCommand("session-info"),
        InterfaceCommand("summarize"),
        InterfaceCommand("quota", "100"),
        InterfaceCommand("quota"),
        InterfaceCommand("quota", "reset"),
        InterfaceCommand("tag", "add 1 bug"),
        InterfaceCommand("tags", "bug"),
        InterfaceCommand("tag", "remove 1 bug"),
        InterfaceCommand("events", "20 model"),
        InterfaceCommand("export", "md"),
        InterfaceCommand("archives"),
        InterfaceCommand("plugins"),
        InterfaceCommand("session-check"),
        InterfaceCommand("workflows", "review"),
        InterfaceCommand("workflow", "use review_changes"),
        InterfaceCommand("workflow", "status"),
        InterfaceCommand("workflow", "auto"),
        InterfaceCommand("workflow", "invalid syntax"),
    ]
    for command in commands:
        _handle_plain_command(cast(Runtime, runtime), cast(AgentService, agent), command)

    other = Session("b" * 32, str(tmp_path), "model")
    runtime.sessions.save(other)
    _handle_plain_command(
        cast(Runtime, runtime),
        cast(AgentService, agent),
        InterfaceCommand("archive", other.session_id),
    )
    _handle_plain_command(
        cast(Runtime, runtime),
        cast(AgentService, agent),
        InterfaceCommand("restore", other.session_id),
    )
    bad = tmp_path / ".harness" / "sessions" / f"{'c' * 32}.json"
    bad.write_text("bad", encoding="utf-8")
    finding = runtime.session_service.scan()[0]
    _handle_plain_command(
        cast(Runtime, runtime),
        cast(AgentService, agent),
        InterfaceCommand("session-check", f"quarantine {finding.check_id}"),
    )

    output = capsys.readouterr().out
    assert "LLM summary" in output
    assert "Exported:" in output
    assert "Archived session" in output
    assert "Restored session" in output
    assert "Quarantined:" in output
    assert "sample" in output
    assert "review_changes" in output
    assert "Next workflow=review_changes" in output


@dataclass
class PluginTool:
    """Configurable plugin tool for validation and wrapper tests."""

    definition_value: ToolDefinition
    result: object = ToolResult("ok")

    @property
    def definition(self) -> ToolDefinition:
        """Return configured schema."""
        return self.definition_value

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Return or raise the configured behavior."""
        if isinstance(self.result, Exception):
            raise self.result
        return cast(ToolResult, self.result)


class Entry:
    """Return a configured plugin factory."""

    name = "plugin"

    def __init__(self, tools: list[PluginTool]) -> None:
        self.tools = tools

    def load(self) -> object:
        """Return the factory."""
        return lambda context: self.tools


def _schema(name: str = "plugin_tool") -> ToolDefinition:
    return ToolDefinition(
        name,
        "Plugin tool",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )


def test_plugin_validation_and_exception_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid schemas fail startup and runtime exceptions become bounded errors."""
    context = PluginContext(1, "C:\\work", 30)
    failing = PluginTool(_schema(), RuntimeError("token=secret-value"))
    entry = Entry([failing])
    monkeypatch.setattr(
        "local_harness.infrastructure.plugins.metadata.entry_points",
        lambda **kwargs: [entry],
    )
    tools, _ = load_plugins(("plugin",), context, SecretRedactor(("secret-value",)), set())
    result = tools[0].execute({})
    assert result.is_error and "secret-value" not in result.content

    entry.tools = [PluginTool(ToolDefinition("Bad", "x", {"type": "object"}))]
    with pytest.raises(ConfigurationError, match="invalid"):
        load_plugins(("plugin",), context, SecretRedactor(), set())

    entry.tools = [PluginTool(_schema("existing"))]
    with pytest.raises(ConfigurationError, match="duplicate"):
        load_plugins(("plugin",), context, SecretRedactor(), {"existing"})


def test_session_maintenance_rejections_and_stale_findings(tmp_path: Path) -> None:
    """Maintenance rejects unsafe formats, collisions, denial, and stale identities."""
    redactor = SecretRedactor()
    repository = JsonSessionRepository(tmp_path, redactor)
    files = SessionFileService(tmp_path, repository, redactor)
    session = Session("d" * 32, str(tmp_path), "model")
    repository.save(session)

    with pytest.raises(SessionError, match="format"):
        files.export(session, "html")
    rejecting = SessionService(repository, files, files, files, Approval(False))
    with pytest.raises(SessionError, match="rejected"):
        rejecting.archive(session.session_id)
    with pytest.raises(SessionError, match="missing or stale"):
        rejecting.quarantine("unknown")

    bad = tmp_path / ".harness" / "sessions" / f"{'e' * 32}.json"
    bad.write_text("bad", encoding="utf-8")
    finding = files.scan()[0]
    with pytest.raises(SessionError, match="rejected"):
        rejecting.quarantine(finding.check_id)
    bad.write_text("changed", encoding="utf-8")
    with pytest.raises(SessionError, match="changed"):
        files.quarantine(finding.check_id)

    archive_dir = tmp_path / ".harness" / "archive"
    archive_dir.mkdir(parents=True)
    (archive_dir / "orphan.zip").write_bytes(b"bad")
    assert any(item.reason == "Archive pair is incomplete" for item in files.scan())


def test_schema_v5_rejects_invalid_analytics(tmp_path: Path) -> None:
    """Corrupt tags, usage, summaries, and quotas do not enter domain state."""
    repository = JsonSessionRepository(tmp_path, SecretRedactor())
    session = Session(
        "1" * 32,
        str(tmp_path),
        "model",
        events=[ProgressEvent(1, 1, "model_complete", "Done", "final", "success")],
    )
    repository.save(session)
    path = tmp_path / ".harness" / "sessions" / f"{session.session_id}.json"
    original = json.loads(path.read_text(encoding="utf-8"))
    mutations = [
        ("events", [{**original["events"][0], "tags": ["Bad Tag"]}]),
        ("events", [{**original["events"][0], "input_tokens": -1}]),
        ("token_budget_override", 0),
        ("summary", {"text": "x", "generation": "bad", "updated_at": "now"}),
    ]
    for key, value in mutations:
        payload = dict(original)
        payload[key] = value
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(SessionError):
            repository.load(session.session_id)
