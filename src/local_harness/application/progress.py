"""Summary protocol helpers for observable agent progress."""

from __future__ import annotations

import re

from local_harness.domain.models import ToolDefinition

_SUMMARY_LINE = re.compile(r"^<step_summary>(.*?)</step_summary>\s*", re.I | re.S)
_MAX_SUMMARY_WORDS = 12
_MAX_SUMMARY_CHARS = 120


def add_step_summary_schema(definition: ToolDefinition) -> ToolDefinition:
    """Return a tool schema requiring orchestration-only summary metadata."""
    parameters = dict(definition.parameters)
    raw_properties = parameters.get("properties", {})
    properties = dict(raw_properties) if isinstance(raw_properties, dict) else {}
    properties["step_summary"] = {
        "type": "string",
        "description": "Observable action summary in at most 12 words; never include reasoning",
        "maxLength": _MAX_SUMMARY_CHARS,
    }
    raw_required = parameters.get("required", [])
    required = list(raw_required) if isinstance(raw_required, list) else []
    if "step_summary" not in required:
        required.append("step_summary")
    parameters["properties"] = properties
    parameters["required"] = required
    return ToolDefinition(
        name=definition.name,
        description=definition.description,
        parameters=parameters,
    )


def normalize_summary(value: object, fallback: str) -> str:
    """Normalize untrusted model text to one short terminal-safe line."""
    if not isinstance(value, str):
        value = fallback
    words = value.replace("\r", " ").replace("\n", " ").split()
    summary = " ".join(words[:_MAX_SUMMARY_WORDS]).strip() or fallback
    return summary[:_MAX_SUMMARY_CHARS]


def extract_final_summary(content: str | None) -> tuple[str, str]:
    """Extract and remove a leading final-response summary marker."""
    if not content:
        return "Completed response", ""
    match = _SUMMARY_LINE.match(content)
    if match is None:
        return "Completed response", content
    summary = normalize_summary(match.group(1), "Completed response")
    return summary, content[match.end() :].lstrip()
