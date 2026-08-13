"""Tests for the provider-neutral agent loop."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from local_harness.application.agent import AgentService
from local_harness.application.ports import ProgressSink
from local_harness.application.tool_registry import ToolRegistry
from local_harness.domain.errors import ModelError, TaskCancelledError
from local_harness.domain.models import (
    Message,
    ProgressEvent,
    Session,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


@dataclass
class FakeModel:
    """Return queued messages while recording model context."""

    replies: list[Message]
    calls: list[Sequence[Message]] = field(default_factory=list)
    tool_definitions: list[Sequence[ToolDefinition]] = field(default_factory=list)

    def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> Message:
        """Return the next queued reply."""
        self.calls.append(messages)
        self.tool_definitions.append(tools)
        assert tools
        return self.replies.pop(0)


@dataclass
class FakeTool:
    """Record execution arguments and return a fixed result."""

    calls: list[Mapping[str, object]] = field(default_factory=list)

    @property
    def definition(self) -> ToolDefinition:
        """Describe the fake inspection tool."""
        return ToolDefinition(
            "inspect",
            "inspect",
            {"type": "object", "properties": {}, "additionalProperties": False},
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Record arguments and return an observation."""
        self.calls.append(arguments)
        return ToolResult("observed")


@dataclass
class MemorySessions:
    """Capture persistence calls without I/O."""

    saves: int = 0

    def save(self, session: Session) -> None:
        """Count one save operation."""
        self.saves += 1

    def load(self, session_id: str) -> Session:
        """Fail because loading is outside these tests."""
        raise AssertionError("not used")

    def list_sessions(self) -> list[Session]:
        """Return no in-memory sessions."""
        return []


@dataclass
class MemoryProgress(ProgressSink):
    """Capture published progress events."""

    events: list[ProgressEvent] = field(default_factory=list)

    def publish(self, event: ProgressEvent) -> None:
        """Store one event."""
        self.events.append(event)


class FakeClock:
    """Return deterministic monotonic timestamps."""

    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        """Return the next configured timestamp."""
        return next(self._values)


def _session() -> Session:
    return Session("a" * 32, "C:\\work", "model")


def test_agent_returns_direct_answer_and_persists_messages() -> None:
    """A no-tool response completes after one model call."""
    model = FakeModel(
        [
            Message(
                role="assistant", content="<step_summary>Answered question</step_summary>\nanswer"
            )
        ]
    )
    sessions = MemorySessions()
    progress = MemoryProgress()
    service = AgentService(
        model_client=model,
        registry=ToolRegistry([FakeTool()]),
        sessions=sessions,
        session=_session(),
        system_prompt="system",
        max_turns=2,
        progress_sink=progress,
        clock=FakeClock(1.0, 2.25),
    )

    assert service.submit("question") == "answer"
    assert sessions.saves == 5
    assert model.calls[0][0].role == "system"
    required = cast(list[str], model.tool_definitions[0][0].parameters["required"])
    assert "step_summary" in required
    assert service.session.messages[-1].content == "answer"
    assert service.session.messages[0].request_number == 1
    assert service.session.messages[-1].request_number == 1
    assert {event.request_number for event in progress.events} == {1}
    assert service.next_request_number == 2
    assert [event.kind for event in progress.events] == ["model_start", "model_complete"]
    assert progress.events[-1].summary == "Answered question"
    assert progress.events[-1].duration_ms == 1250


def test_agent_uses_one_quality_correction_and_persists_only_selected_answer() -> None:
    """An invalid final answer is corrected once without duplicate transcript entries."""
    model = FakeModel(
        [
            Message(role="assistant", content="<p>Unformatted result</p>"),
            Message(role="assistant", content="## Result\n\nFormatted result."),
        ]
    )
    progress = MemoryProgress()
    service = AgentService(
        model_client=model,
        registry=ToolRegistry([FakeTool()]),
        sessions=MemorySessions(),
        session=_session(),
        system_prompt="system",
        max_turns=2,
        progress_sink=progress,
    )

    assert service.submit("format this") == "## Result\n\nFormatted result."
    assert len(model.calls) == 2
    assert "Rewrite your previous proposed final answer" in (model.calls[1][-1].content or "")
    assistant_messages = [
        message for message in service.session.messages if message.role == "assistant"
    ]
    assert [message.content for message in assistant_messages] == ["## Result\n\nFormatted result."]
    assert progress.events[-1].status == "success"


def test_agent_preserves_substantive_answer_when_quality_correction_fails() -> None:
    """A failed correction returns the normalized original with a visible warning."""
    model = FakeModel(
        [
            Message(role="assistant", content="Useful result<br>Still useful"),
            Message(role="assistant", content="<div>Still invalid</div>"),
        ]
    )
    progress = MemoryProgress()
    service = AgentService(
        model_client=model,
        registry=ToolRegistry([FakeTool()]),
        sessions=MemorySessions(),
        session=_session(),
        system_prompt="system",
        max_turns=2,
        progress_sink=progress,
    )

    assert service.submit("answer safely") == "Useful result\n\nStill useful"
    assert service.session.messages[-1].content == "Useful result\n\nStill useful"
    assert progress.events[-1].status == "warning"
    assert progress.events[-1].summary == "Answer completed with formatting warning"


def test_agent_executes_tool_and_feeds_result_back() -> None:
    """Valid tool calls are executed sequentially before the final reply."""
    tool = FakeTool()
    model = FakeModel(
        [
            Message(
                role="assistant",
                tool_calls=(
                    ToolCall(
                        "call-1",
                        "inspect",
                        '{"path":".","step_summary":"Inspecting project"}',
                    ),
                ),
            ),
            Message(
                role="assistant", content="<step_summary>Completed inspection</step_summary>\ndone"
            ),
        ]
    )
    progress = MemoryProgress()
    service = AgentService(
        model_client=model,
        registry=ToolRegistry([tool]),
        sessions=MemorySessions(),
        session=_session(),
        system_prompt="system",
        max_turns=3,
        progress_sink=progress,
        clock=FakeClock(1.0, 2.0, 3.0, 3.5, 4.0, 5.0),
    )

    assert service.submit("inspect") == "done"
    assert tool.calls == [{"path": "."}]
    assert model.calls[1][-1].content == "observed"
    assert [event.kind for event in progress.events] == [
        "model_start",
        "model_complete",
        "tool_complete",
        "model_start",
        "model_complete",
    ]
    assert progress.events[1].summary == "Inspecting project"
    assert progress.events[2].duration_ms == 500
    assert {message.request_number for message in service.session.messages} == {1}


def test_agent_converts_bad_calls_to_tool_errors() -> None:
    """Malformed JSON and unknown tools remain inside the conversation loop."""
    model = FakeModel(
        [
            Message(
                role="assistant",
                tool_calls=(ToolCall("bad", "missing", "[]"),),
            ),
            Message(role="assistant", content="recovered"),
        ]
    )
    service = AgentService(
        model_client=model,
        registry=ToolRegistry([FakeTool()]),
        sessions=MemorySessions(),
        session=_session(),
        system_prompt="system",
        max_turns=2,
    )

    assert service.submit("bad") == "recovered"
    assert "Tool call rejected" in (model.calls[1][-1].content or "")


def test_agent_repairs_unique_placeholder_tool_name() -> None:
    """A placeholder name is repaired only when one schema matches its arguments."""
    tool = FakeTool()
    model = FakeModel(
        [
            Message(
                role="assistant",
                tool_calls=(ToolCall("bad-name", "?", '{"step_summary":"Inspecting project"}'),),
            ),
            Message(role="assistant", content="recovered"),
        ]
    )
    service = AgentService(
        model_client=model,
        registry=ToolRegistry([tool]),
        sessions=MemorySessions(),
        session=_session(),
        system_prompt="system",
        max_turns=2,
    )

    assert service.submit("inspect") == "recovered"
    assert tool.calls == [{}]
    assert service.session.messages[1].tool_calls[0].name == "inspect"


def test_agent_retries_empty_final_response() -> None:
    """An empty response is observable and retried instead of becoming a blank answer."""
    model = FakeModel(
        [
            Message(role="assistant", content=""),
            Message(
                role="assistant",
                content="<step_summary>Answered after retry</step_summary>\nUseful answer",
            ),
        ]
    )
    progress = MemoryProgress()
    service = AgentService(
        model_client=model,
        registry=ToolRegistry([FakeTool()]),
        sessions=MemorySessions(),
        session=_session(),
        system_prompt="system",
        max_turns=2,
        progress_sink=progress,
    )

    assert service.submit("answer") == "Useful answer"
    assert model.calls[1][-1].role == "user"
    assert "previous response was empty" in (model.calls[1][-1].content or "")
    assert [event.kind for event in progress.events] == [
        "model_start",
        "model_error",
        "model_start",
        "model_complete",
    ]
    assert [message.role for message in service.session.messages] == ["user", "assistant"]


def test_agent_stops_at_turn_limit_and_handles_empty_input() -> None:
    """The loop cannot execute indefinitely and ignores blank prompts."""
    model = FakeModel([Message(role="assistant", tool_calls=(ToolCall("1", "inspect", "{}"),))])
    service = AgentService(
        model_client=model,
        registry=ToolRegistry([FakeTool()]),
        sessions=MemorySessions(),
        session=_session(),
        system_prompt="system",
        max_turns=1,
    )

    assert "Please enter" in service.submit(" ")
    assert "Stopped after 1 LLM calls" in service.submit("loop")


def test_agent_persists_and_resets_runtime_limit() -> None:
    """Runtime configuration updates the next-request limit and saved session."""
    session = _session()
    sessions = MemorySessions()
    service = AgentService(
        model_client=FakeModel([Message(role="assistant", content="unused")]),
        registry=ToolRegistry([FakeTool()]),
        sessions=sessions,
        session=session,
        system_prompt="system",
        max_turns=20,
        max_turns_source=".env",
    )

    assert service.configure_max_turns(30) == 30
    assert service.max_turns_source == "session"
    assert session.max_turns_override == 30
    assert service.configure_max_turns(None) == 20
    assert service.max_turns_source == ".env"
    assert session.max_turns_override is None
    try:
        service.configure_max_turns(101)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid runtime limit was accepted")


def test_agent_continues_call_numbers_and_records_model_errors() -> None:
    """Resumed event history determines numbering and failures remain observable."""

    @dataclass
    class FailingModel:
        """Raise an expected provider failure."""

        def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> Message:
            """Fail every model call."""
            raise ModelError("offline")

    session = _session()
    session.messages.append(Message(role="user", content="legacy"))
    session.events.append(ProgressEvent(1, 7, "model_complete", "Earlier", "final", "success"))
    progress = MemoryProgress()
    service = AgentService(
        model_client=FailingModel(),
        registry=ToolRegistry([FakeTool()]),
        sessions=MemorySessions(),
        session=session,
        system_prompt="system",
        max_turns=1,
        progress_sink=progress,
        clock=FakeClock(4.0, 4.5),
    )

    try:
        service.submit("fail")
    except ModelError:
        pass
    else:
        raise AssertionError("model failure did not propagate")
    assert progress.events[0].call_number == 8
    assert progress.events[-1].kind == "model_error"
    assert progress.events[-1].duration_ms == 500
    assert progress.events[-1].request_number == 2


def test_tool_registry_rejects_duplicates() -> None:
    """Duplicate model tool names cannot shadow each other."""
    try:
        ToolRegistry([FakeTool(), FakeTool()])
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate registry was accepted")


def test_agent_cancellation_prevents_the_next_model_boundary() -> None:
    """A cooperative cancellation signal prevents provider and tool work."""
    model = FakeModel([Message("assistant", "must not run")])
    service = AgentService(
        model_client=model,
        registry=ToolRegistry([FakeTool()]),
        sessions=MemorySessions(),
        session=_session(),
        system_prompt="system",
        max_turns=2,
        cancellation_requested=lambda: True,
    )
    try:
        service.submit("cancel me")
    except TaskCancelledError:
        pass
    else:
        raise AssertionError("cancelled request reached the provider boundary")
    assert model.calls == []


def test_agent_applies_voice_profile_final_answer_bound() -> None:
    """Bound the normalized final result after evidence text is appended."""
    model = FakeModel([Message("assistant", "<step_summary>done</step_summary>\n" + "x" * 900)])
    service = AgentService(
        model_client=model,
        registry=ToolRegistry([FakeTool()]),
        sessions=MemorySessions(),
        session=_session(),
        system_prompt="system",
        max_turns=1,
        max_answer_chars=500,
    )
    assert len(service.submit("bounded answer")) <= 500
