"""OpenAI-compatible chat-completions model adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from openai import OpenAI

from local_harness.domain.errors import ModelError
from local_harness.domain.models import (
    Message,
    ModelCompletion,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class OpenAIModelClient:
    """Adapt an OpenAI-compatible endpoint to the model port."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        """Create a client for one configured model."""
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    @property
    def model(self) -> str:
        """Return the configured LiteLLM model alias without exposing credentials."""
        return self._model

    def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelCompletion:
        """Return the next assistant message from Chat Completions."""
        request: dict[str, object] = {
            "model": self._model,
            "messages": [_message_to_openai(message) for message in messages],
        }
        if tools:
            request["tools"] = [_tool_to_openai(tool) for tool in tools]
            request["tool_choice"] = "auto"
        try:
            response = self._client.chat.completions.create(**cast(Any, request))
        except Exception as exc:
            raise ModelError(f"Model request failed: {type(exc).__name__}: {exc}") from exc
        if not response.choices:
            raise ModelError("Model returned no choices")
        reply = response.choices[0].message
        tool_calls = tuple(_tool_call_from_openai(call) for call in (reply.tool_calls or []))
        usage = None
        response_usage = getattr(response, "usage", None)
        if response_usage is not None:
            usage = TokenUsage(
                input_tokens=max(0, response_usage.prompt_tokens),
                output_tokens=max(0, response_usage.completion_tokens),
                source="provider",
            )
        return ModelCompletion(
            Message(role="assistant", content=reply.content, tool_calls=tool_calls), usage
        )


def _tool_call_from_openai(call: object) -> ToolCall:
    raw_call = cast(Any, call)
    if getattr(raw_call, "type", "function") != "function":
        raise ModelError("Model returned an unsupported custom tool call")
    return ToolCall(
        id=raw_call.id,
        name=raw_call.function.name,
        arguments=raw_call.function.arguments,
    )


def _message_to_openai(message: Message) -> dict[str, object]:
    data: dict[str, object] = {"role": message.role}
    if message.content is not None:
        data["content"] = message.content
    if message.tool_calls:
        data["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        data["tool_call_id"] = message.tool_call_id
    if message.name is not None:
        data["name"] = message.name
    return data


def _tool_to_openai(tool: ToolDefinition) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }
