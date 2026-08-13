"""Tests for OpenAI wire-format translation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from local_harness.domain.errors import ModelError
from local_harness.domain.models import Message, ToolCall, ToolDefinition
from local_harness.infrastructure import openai_model
from local_harness.infrastructure.openai_model import (
    OpenAIModelClient,
    _message_to_openai,
    _tool_to_openai,
)


def test_message_translation_preserves_tool_protocol_fields() -> None:
    """Provider-neutral assistant and tool messages map to Chat Completions."""
    assistant = Message(
        role="assistant",
        content=None,
        tool_calls=(ToolCall("id", "inspect", "{}"),),
        request_number=2,
    )
    tool = Message(role="tool", content="ok", tool_call_id="id", name="inspect")

    assert _message_to_openai(assistant)["tool_calls"] == [
        {
            "id": "id",
            "type": "function",
            "function": {"name": "inspect", "arguments": "{}"},
        }
    ]
    assert _message_to_openai(tool)["tool_call_id"] == "id"
    assert "request_number" not in _message_to_openai(assistant)


def test_tool_translation_uses_function_schema() -> None:
    """Tool definitions map to the OpenAI function shape."""
    result = _tool_to_openai(ToolDefinition("name", "description", {"type": "object"}))

    assert result["type"] == "function"
    assert result["function"] == {
        "name": "name",
        "description": "description",
        "parameters": {"type": "object"},
    }


class FakeCompletions:
    """Return a configured fake SDK response."""

    def __init__(self, response: object = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> object:
        """Record request arguments and return or raise."""
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


class FakeOpenAI:
    """Provide the nested SDK chat-completions surface."""

    completions: FakeCompletions

    def __init__(self, base_url: str, api_key: str) -> None:
        """Expose the configured global fake completions object."""
        self.chat = SimpleNamespace(completions=self.completions)


def test_model_client_completes_and_decodes_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter sends schemas and converts an SDK response to domain models."""
    function = SimpleNamespace(name="inspect", arguments="{}")
    message = SimpleNamespace(
        content="thinking",
        tool_calls=[SimpleNamespace(id="call", type="function", function=function)],
    )
    completions = FakeCompletions(
        SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4),
        )
    )
    FakeOpenAI.completions = completions
    monkeypatch.setattr(openai_model, "OpenAI", FakeOpenAI)
    client = OpenAIModelClient("http://local/v1", "key", "model")

    result = client.complete(
        [Message(role="user", content="hi")],
        [ToolDefinition("inspect", "inspect", {"type": "object"})],
    )

    assert result.tool_calls[0].name == "inspect"
    assert result.usage is not None and result.usage.total_tokens == 14
    assert completions.kwargs["model"] == "model"


def test_empty_tools_are_omitted_from_provider_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A protected no-tool workflow sends no tool configuration to the provider."""
    completions = FakeCompletions(
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="answer", tool_calls=[]))]
        )
    )
    FakeOpenAI.completions = completions
    monkeypatch.setattr("local_harness.infrastructure.openai_model.OpenAI", FakeOpenAI)

    OpenAIModelClient("http://local/v1", "key", "model").complete([], [])

    assert "tools" not in completions.kwargs
    assert "tool_choice" not in completions.kwargs


def test_model_client_translates_provider_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK failures, empty choices, and custom calls become model errors."""
    completions = FakeCompletions(error=RuntimeError("offline"))
    FakeOpenAI.completions = completions
    monkeypatch.setattr(openai_model, "OpenAI", FakeOpenAI)
    with pytest.raises(ModelError, match="Model request failed"):
        OpenAIModelClient("http://local/v1", "key", "model").complete([], [])

    completions.error = None
    completions.response = SimpleNamespace(choices=[])
    with pytest.raises(ModelError, match="no choices"):
        OpenAIModelClient("http://local/v1", "key", "model").complete([], [])

    custom = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(type="custom")])
    completions.response = SimpleNamespace(choices=[SimpleNamespace(message=custom)])
    with pytest.raises(ModelError, match="custom tool"):
        OpenAIModelClient("http://local/v1", "key", "model").complete([], [])
