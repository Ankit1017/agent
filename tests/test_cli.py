"""Tests for interactive command routing and top-level error translation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from local_harness.application.agent import AgentService
from local_harness.bootstrap import Runtime
from local_harness.domain.errors import ConfigurationError, SessionError
from local_harness.domain.models import Message, ProgressEvent, Session
from local_harness.interfaces import cli


@dataclass
class FakeAgent:
    """Minimal interactive agent test double."""

    session: Session
    max_turns: int = 20
    max_turns_source: str = "default"

    def submit(self, value: str) -> str:
        """Echo a deterministic reply."""
        return f"reply:{value}"

    def configure_max_turns(self, value: int | None) -> int:
        """Apply or reset an in-memory test limit."""
        if value is None:
            self.max_turns = 20
            self.max_turns_source = "default"
            self.session.max_turns_override = None
        else:
            self.max_turns = value
            self.max_turns_source = "session"
            self.session.max_turns_override = value
        return self.max_turns


class FakeSessions:
    """In-memory session command test double."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.session = Session("a" * 32, str(workspace), "model")
        self.session.messages.append(Message(role="user", content="preview"))
        self.session.events.append(
            ProgressEvent(1, 1, "model_complete", "Earlier response", "final", "success")
        )

    def load(self, session_id: str) -> Session:
        """Load the one known session or fail."""
        if session_id != self.session.session_id:
            raise SessionError("not found")
        return self.session

    def list_sessions(self) -> list[Session]:
        """Return the one known session."""
        return [self.session]


class FakeRuntime:
    """Provide the subset of runtime used by the REPL."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.settings = SimpleNamespace(model="model", models=("model", "other"))
        self.sessions = FakeSessions(workspace)
        self.counter = 0

    def new_session(self) -> Session:
        """Create a distinct session."""
        self.counter += 1
        return Session(f"{self.counter:032x}", str(self.workspace), "model")

    def agent(self, session: Session) -> FakeAgent:
        """Bind a fake agent to a session."""
        return FakeAgent(session)

    def switch_model(self, session: Session, model: str | None) -> FakeAgent:
        """Apply a configured model selection in the fake runtime."""
        selected = self.settings.model if model is None else model
        if selected not in self.settings.models:
            raise ConfigurationError("model is not configured")
        session.model = selected
        return self.agent(session)


def test_repl_routes_commands_and_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """All documented slash commands route without invoking the model."""
    runtime = FakeRuntime(tmp_path)
    known_id = runtime.sessions.session.session_id
    inputs = iter(
        [
            "/help",
            "/sessions",
            "/events",
            "/events 0",
            "/max-turns",
            "/max-turns 30",
            "/max-turns 101",
            "/max-turns reset",
            "/models",
            "/model",
            "/model other",
            "/model missing",
            "/model reset",
            "/new",
            "/resume missing",
            f"/resume {known_id}",
            "/unknown",
            "hello",
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    cli._repl(cast(Runtime, runtime), cast(AgentService, FakeAgent(runtime.sessions.session)))

    output = capsys.readouterr().out
    assert "/help" in output
    assert "Started session" in output
    assert "Earlier response" in output
    assert "Usage: /events" in output
    assert "max LLM calls/request=30" in output
    assert "max turns must be between 1 and 100" in output
    assert "model=other" in output
    assert "model is not configured" in output
    assert "Error: not found" in output
    assert "reply:hello" in output
    assert "Goodbye" in output


def test_main_builds_resumed_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The top-level entry point loads a requested session and starts the REPL."""
    runtime = FakeRuntime(tmp_path)
    called: list[str] = []
    overrides: list[int | None] = []

    def fake_build_runtime(_path: Path, *, max_turns_override: int | None = None) -> Runtime:
        overrides.append(max_turns_override)
        return cast(Runtime, runtime)

    monkeypatch.setattr(cli, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(
        cli,
        "_repl",
        lambda _runtime, agent, **_kwargs: called.append(agent.session.session_id),
    )

    cli.main(["--resume", runtime.sessions.session.session_id, "--max-turns", "30"])

    assert called == [runtime.sessions.session.session_id]
    assert overrides == [30]


def test_print_events_handles_empty_history(capsys: pytest.CaptureFixture[str]) -> None:
    """Event review explains an empty session instead of printing nothing."""
    cli._print_events([])
    assert "No progress events" in capsys.readouterr().out


def test_main_translates_errors_and_interrupts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Expected startup failures exit cleanly and interrupts do not show tracebacks."""
    monkeypatch.setattr(
        cli,
        "build_runtime",
        lambda _path, **_kwargs: (_ for _ in ()).throw(ConfigurationError("bad")),
    )
    with pytest.raises(SystemExit):
        cli.main([])
    assert "Error: bad" in capsys.readouterr().err

    monkeypatch.setattr(
        cli, "build_runtime", lambda _path, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    cli.main([])
    assert "Stopped" in capsys.readouterr().out
