"""Tests for versioned JSON session storage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_harness.domain.errors import SessionError
from local_harness.domain.models import (
    CompletionEvidence,
    Message,
    ProgressEvent,
    Session,
    SessionSummary,
    TaskPlan,
    TaskStep,
    ToolCall,
)
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.json_sessions import JsonSessionRepository


def test_session_round_trip_redacts_and_lists(tmp_path: Path) -> None:
    """Full transcripts survive atomic persistence without stored secrets."""
    repository = JsonSessionRepository(tmp_path, SecretRedactor(("secret-value",)))
    session = Session(
        "a" * 32,
        str(tmp_path),
        "model",
        messages=[
            Message(
                role="assistant",
                content="secret-value",
                tool_calls=(ToolCall("call", "tool", '{"token":"secret-value"}'),),
                request_number=1,
            )
        ],
        events=[
            ProgressEvent(
                1,
                1,
                "model_complete",
                "secret-value",
                "final",
                "success",
                100,
                request_number=1,
            )
        ],
    )

    repository.save(session)
    stored = (tmp_path / ".harness" / "sessions" / f"{session.session_id}.json").read_text()
    loaded = repository.load(session.session_id)

    assert "secret-value" not in stored
    assert loaded.messages[0].content == "[REDACTED]"
    assert loaded.events[0].summary == "[REDACTED]"
    assert loaded.messages[0].request_number == 1
    assert loaded.events[0].request_number == 1
    assert loaded.schema_version == 7
    assert repository.list_sessions()[0].session_id == session.session_id


def test_session_repository_rejects_ids_missing_and_corruption(tmp_path: Path) -> None:
    """Invalid identifiers and unsupported documents become domain errors."""
    repository = JsonSessionRepository(tmp_path, SecretRedactor())
    with pytest.raises(SessionError):
        repository.load("../escape")
    with pytest.raises(SessionError):
        repository.load("b" * 32)
    directory = tmp_path / ".harness" / "sessions"
    directory.mkdir(parents=True)
    bad_id = "c" * 32
    (directory / f"{bad_id}.json").write_text("not json", encoding="utf-8")
    with pytest.raises(SessionError):
        repository.load(bad_id)
    assert repository.list_sessions() == []


def test_session_repository_migrates_old_versions_and_rejects_unknown_schema(
    tmp_path: Path,
) -> None:
    """Version-one and two documents gain defaults while unknown versions fail."""
    repository = JsonSessionRepository(tmp_path, SecretRedactor())
    session_id = "d" * 32
    directory = tmp_path / ".harness" / "sessions"
    directory.mkdir(parents=True)
    v1 = Session(session_id, str(tmp_path), "model")
    repository.save(v1)
    path = directory / f"{session_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    payload.pop("events")
    path.write_text(json.dumps(payload), encoding="utf-8")
    migrated = repository.load(session_id)
    assert migrated.schema_version == 7
    assert migrated.events == []
    assert migrated.max_turns_override is None

    payload["schema_version"] = 2
    payload["events"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert repository.load(session_id).max_turns_override is None

    payload["schema_version"] = 3
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert repository.load(session_id).max_turns_override is None

    payload["schema_version"] = 8
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SessionError):
        repository.load(session_id)


def test_session_repository_validates_v3_turn_override(tmp_path: Path) -> None:
    """Valid overrides round-trip and corrupt limits are rejected."""
    repository = JsonSessionRepository(tmp_path, SecretRedactor())
    session = Session("e" * 32, str(tmp_path), "model", max_turns_override=30)
    repository.save(session)
    assert repository.load(session.session_id).max_turns_override == 30

    path = tmp_path / ".harness" / "sessions" / f"{session.session_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["max_turns_override"] = 101
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SessionError):
        repository.load(session.session_id)


def test_session_repository_validates_v4_request_numbers(tmp_path: Path) -> None:
    """Request grouping accepts positive integers and rejects corrupt values."""
    repository = JsonSessionRepository(tmp_path, SecretRedactor())
    session = Session(
        "f" * 32,
        str(tmp_path),
        "model",
        messages=[Message(role="user", content="hello", request_number=1)],
    )
    repository.save(session)
    path = tmp_path / ".harness" / "sessions" / f"{session.session_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["messages"][0]["request_number"] = 0
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SessionError):
        repository.load(session.session_id)


def test_session_repository_round_trips_schema_v6_analytics(tmp_path: Path) -> None:
    """Schema six persists summaries, event tags, usage, and quota overrides."""
    repository = JsonSessionRepository(tmp_path, SecretRedactor())
    session = Session(
        "1" * 32,
        str(tmp_path),
        "model",
        token_budget_override=500,
        summary=SessionSummary("Work completed", "llm"),
        plans=[TaskPlan(1, "Verify change", (TaskStep(1, "Run tests", "completed"),), "completed")],
        evidence=[CompletionEvidence(1, ("app.py",), ("tests: completed",))],
        events=[
            ProgressEvent(
                1,
                1,
                "model_complete",
                "Done",
                "final",
                "success",
                tags=("idea",),
                input_tokens=10,
                output_tokens=5,
                usage_source="provider",
            )
        ],
    )

    repository.save(session)
    loaded = repository.load(session.session_id)

    assert loaded.schema_version == 7
    assert loaded.summary is not None and loaded.summary.text == "Work completed"
    assert loaded.token_budget_override == 500
    assert loaded.events[0].tags == ("idea",)
    assert loaded.events[0].input_tokens == 10
    assert loaded.plans[0].steps[0].description == "Run tests"
    assert loaded.evidence[0].changed_files == ("app.py",)
