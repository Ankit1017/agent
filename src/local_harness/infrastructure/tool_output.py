"""Compact versioned JSON envelopes for model-facing tool results."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from local_harness.guardrails.redaction import SecretRedactor


def tool_envelope(
    summary: str,
    items: Sequence[Mapping[str, object]],
    *,
    max_chars: int,
    redactor: SecretRedactor,
    truncated: bool = False,
    next_cursor: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> str:
    """Serialize a redacted tool result while dropping tail items to fit its budget."""
    included = list(items)
    was_truncated = truncated
    while True:
        payload: dict[str, object] = {
            "version": 1,
            "summary": summary,
            "items": included,
            "truncated": was_truncated,
            "next_cursor": next_cursor if was_truncated else None,
            "metadata": dict(metadata or {}),
        }
        rendered = redactor.redact(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        if len(rendered) <= max_chars:
            return rendered
        if included:
            included.pop()
            was_truncated = True
            continue
        payload["summary"] = summary[: max(40, max_chars // 3)]
        payload["metadata"] = {}
        payload["truncated"] = True
        payload["next_cursor"] = next_cursor
        rendered = redactor.redact(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        if len(rendered) <= max_chars:
            return rendered
        payload["summary"] = "Output exceeded the configured limit"
        return redactor.redact(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
