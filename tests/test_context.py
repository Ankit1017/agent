"""Tests for deterministic bounded provider context selection."""

from __future__ import annotations

import json

import pytest

from local_harness.application.context import ContextBuilder
from local_harness.domain.errors import ContextLimitError
from local_harness.domain.models import Message, ToolCall, ToolDefinition


def _tool() -> ToolDefinition:
    return ToolDefinition("read_files", "Read files", {"type": "object"})


def test_context_keeps_current_protocol_and_omits_prior_tool_details() -> None:
    """Completed requests retain only user/final answer while current calls remain paired."""
    messages = [
        Message("user", "old request", request_number=1),
        Message(
            "assistant",
            tool_calls=(ToolCall("old-call", "read_files", "{}"),),
            request_number=1,
        ),
        Message("tool", "old details", tool_call_id="old-call", request_number=1),
        Message("assistant", "old answer", request_number=1),
        Message("user", "current request", request_number=2),
        Message(
            "assistant",
            tool_calls=(ToolCall("new-call", "read_files", "{}"),),
            request_number=2,
        ),
        Message("tool", "current details", tool_call_id="new-call", request_number=2),
    ]

    result = ContextBuilder(10_000).build("system", messages, [_tool()], 2)

    assert [message.role for message in result] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "tool",
    ]
    assert "old details" not in [message.content for message in result]
    assert result[-1].content == "current details"


def test_context_compacts_older_current_tool_results_without_mutation() -> None:
    """Older results compact while the newest result and persisted objects stay complete."""
    large = json.dumps({"summary": "Read data", "items": ["x" * 2_000]})
    messages = [
        Message("user", "inspect", request_number=1),
        Message(
            "assistant",
            tool_calls=(ToolCall("one", "read_files", "{}"),),
            request_number=1,
        ),
        Message("tool", large, tool_call_id="one", request_number=1),
        Message(
            "assistant",
            tool_calls=(ToolCall("two", "read_files", "{}"),),
            request_number=1,
        ),
        Message("tool", "new result", tool_call_id="two", request_number=1),
    ]

    result = ContextBuilder(1_300).build("s", messages, [_tool()], 1)

    assert '"compacted":true' in (result[3].content or "")
    assert result[-1].content == "new result"
    assert messages[2].content == large


def test_context_rejects_oversized_essential_request() -> None:
    """The current prompt is never silently truncated."""
    messages = [Message("user", "x" * 2_000, request_number=1)]

    with pytest.raises(ContextLimitError, match="HARNESS_CONTEXT_MAX_CHARS"):
        ContextBuilder(500).build("system", messages, [_tool()], 1)


def test_context_compacts_web_results_with_citation_metadata() -> None:
    """Older web content keeps bounded citation identity and observable head/tail."""
    payload = json.dumps(
        {
            "version": 1,
            "summary": "Read source",
            "items": [
                {
                    "source_id": "web-1",
                    "title": "Official docs",
                    "final_url": "https://example.com/",
                    "status": "success",
                    "content": "head " + "x" * 2_000 + " tail",
                }
            ],
            "metadata": {"content_is_untrusted": True},
        }
    )
    messages = [
        Message("user", "research", request_number=1),
        Message(
            "assistant",
            tool_calls=(ToolCall("one", "read_web_pages", "{}"),),
            request_number=1,
        ),
        Message("tool", payload, tool_call_id="one", request_number=1),
        Message(
            "assistant",
            tool_calls=(ToolCall("two", "web_search", "{}"),),
            request_number=1,
        ),
        Message("tool", "latest", tool_call_id="two", request_number=1),
    ]

    result = ContextBuilder(1_600).build("system", messages, [_tool()], 1)

    compacted = result[3].content or ""
    assert "web-1" in compacted
    assert "content_head" in compacted
    assert result[-1].content == "latest"
