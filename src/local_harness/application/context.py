"""Deterministic provider-context selection without rewriting session history."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, replace

from local_harness.domain.errors import ContextLimitError
from local_harness.domain.models import Message, ToolDefinition
from local_harness.text import truncate_text

_COMPACT_TOOL_CHARS = 1_500


class ContextBuilder:
    """Build a protocol-valid, character-bounded view of persisted messages."""

    def __init__(self, max_chars: int) -> None:
        """Set the complete input budget including system text and tool schemas."""
        if max_chars <= 0:
            raise ValueError("context max chars must be greater than zero")
        self._max_chars = max_chars

    def build(
        self,
        system_prompt: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        current_request_number: int,
        project_context: str = "",
    ) -> list[Message]:
        """Return bounded history plus ephemeral, shrink-first project context."""
        system = Message(role="system", content=system_prompt)
        memory = self._fit_project_context(system, project_context, tools)
        current = [
            message for message in messages if message.request_number == current_request_number
        ]
        essential = [system, *memory, *current]
        if self._size(essential, tools) > self._max_chars:
            memory = []
            essential = [system, *self._compact_current(current, tools, system)]
        if self._size(essential, tools) > self._max_chars:
            raise ContextLimitError(
                "Current request cannot fit HARNESS_CONTEXT_MAX_CHARS; shorten the prompt or "
                "increase the configured context budget"
            )

        selected_prior: list[list[Message]] = []
        for exchange in reversed(_completed_exchanges(messages, current_request_number)):
            candidate = [
                system,
                *memory,
                *[item for group in reversed(selected_prior) for item in group],
            ]
            candidate.extend(exchange)
            candidate.extend(essential[1:])
            if self._size(candidate, tools) <= self._max_chars:
                selected_prior.append(exchange)
        prior = [item for group in reversed(selected_prior) for item in group]
        return [system, *memory, *prior, *essential[len(memory) + 1 :]]

    def _fit_project_context(
        self,
        system: Message,
        project_context: str,
        tools: Sequence[ToolDefinition],
    ) -> list[Message]:
        if not project_context:
            return []
        message = Message(role="system", content=project_context)
        fixed_size = self._size([system], tools)
        available = max(0, self._max_chars - fixed_size - 256)
        if available <= 0:
            return []
        content, _ = truncate_text(project_context, available)
        return [replace(message, content=content)] if content else []

    def _compact_current(
        self,
        current: list[Message],
        tools: Sequence[ToolDefinition],
        system: Message,
    ) -> list[Message]:
        compacted = list(current)
        tool_indexes = [index for index, message in enumerate(compacted) if message.role == "tool"]
        for index in tool_indexes[:-1]:
            compacted[index] = replace(
                compacted[index], content=_compact_tool_content(compacted[index].content or "")
            )
            if self._size([system, *compacted], tools) <= self._max_chars:
                break
        return compacted

    @staticmethod
    def _size(messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> int:
        message_size = len(
            json.dumps([asdict(message) for message in messages], ensure_ascii=False)
        )
        tool_size = len(json.dumps([asdict(tool) for tool in tools], ensure_ascii=False))
        return message_size + tool_size


def _completed_exchanges(
    messages: Sequence[Message], current_request_number: int
) -> list[list[Message]]:
    grouped: dict[int, list[Message]] = {}
    legacy: list[list[Message]] = []
    legacy_current: list[Message] = []
    for message in messages:
        if message.request_number is not None:
            if message.request_number < current_request_number:
                grouped.setdefault(message.request_number, []).append(message)
            continue
        if message.role == "user":
            if legacy_current:
                legacy.append(_final_exchange(legacy_current))
            legacy_current = [message]
        elif legacy_current and message.role == "assistant" and not message.tool_calls:
            legacy_current.append(message)
    if legacy_current:
        legacy.append(_final_exchange(legacy_current))
    tagged = [_final_exchange(grouped[number]) for number in sorted(grouped)]
    return [group for group in [*legacy, *tagged] if len(group) == 2]


def _final_exchange(messages: Sequence[Message]) -> list[Message]:
    user = next((message for message in messages if message.role == "user"), None)
    assistant = next(
        (
            message
            for message in reversed(messages)
            if message.role == "assistant" and not message.tool_calls
        ),
        None,
    )
    return [message for message in (user, assistant) if message is not None]


def _compact_tool_content(content: str) -> str:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        compacted, _ = truncate_text(content, _COMPACT_TOOL_CHARS)
        return compacted
    if not isinstance(payload, dict):
        compacted, _ = truncate_text(content, _COMPACT_TOOL_CHARS)
        return compacted
    summary = payload.get("summary", "Previous tool result compacted")
    items = payload.get("items", [])
    compact_items = _compact_web_items(items, payload.get("metadata"))
    rendered = json.dumps(
        {
            "version": payload.get("version", 1),
            "summary": summary,
            "items_omitted": len(items) if isinstance(items, list) else 0,
            "citation_items": compact_items,
            "compacted": True,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    compacted, _ = truncate_text(rendered, _COMPACT_TOOL_CHARS)
    return compacted


def _compact_web_items(items: object, metadata: object) -> list[dict[str, object]]:
    if not isinstance(metadata, dict) or metadata.get("content_is_untrusted") is not True:
        return []
    if not isinstance(items, list):
        return []
    compacted: list[dict[str, object]] = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        selected = {
            name: item[name]
            for name in ("source_id", "title", "url", "final_url", "status")
            if name in item
        }
        content = item.get("content")
        if isinstance(content, str):
            selected["content_head"] = content[:200]
            selected["content_tail"] = content[-200:]
        compacted.append(selected)
    return compacted
