"""Allowlisted Python entry-point discovery and safe tool wrapping."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from importlib import metadata
from typing import cast

from local_harness.application.ports import Tool
from local_harness.domain.errors import ConfigurationError
from local_harness.domain.maintenance import PluginStatus
from local_harness.domain.models import ToolDefinition, ToolResult
from local_harness.domain.plugins import PluginContext
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.text import truncate_text

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PluginFactory = Callable[[PluginContext], Sequence[Tool]]


class PluginToolAdapter:
    """Bound and redact results from one trusted in-process plugin tool."""

    def __init__(self, tool: Tool, redactor: SecretRedactor, max_output_chars: int) -> None:
        """Store the validated plugin tool and output controls."""
        self._tool = tool
        self._redactor = redactor
        self._max_output_chars = max_output_chars

    @property
    def definition(self) -> ToolDefinition:
        """Return the plugin's validated model schema."""
        return self._tool.definition

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Translate unexpected exceptions and bound all plugin output."""
        try:
            result = self._tool.execute(arguments)
            if not isinstance(result, ToolResult):
                raise TypeError("plugin returned an invalid result")
            content, truncated = truncate_text(
                self._redactor.redact(result.content), self._max_output_chars
            )
            if truncated:
                content += "\n[plugin output truncated]"
            return ToolResult(content, result.is_error)
        except Exception as exc:
            detail = self._redactor.redact(str(exc))
            return ToolResult(f"Plugin tool failed: {type(exc).__name__}: {detail}"[:500], True)


def load_plugins(
    enabled: tuple[str, ...],
    context: PluginContext,
    redactor: SecretRedactor,
    existing_names: set[str],
    *,
    maximum_tools: int = 32,
) -> tuple[list[Tool], list[PluginStatus]]:
    """Discover without importing, then load only explicitly enabled entry points."""
    discovered = {entry.name: entry for entry in metadata.entry_points(group="local_harness.tools")}
    missing = [name for name in enabled if name not in discovered]
    if missing:
        raise ConfigurationError(f"Enabled plugin not installed: {', '.join(missing)}")
    tools: list[Tool] = []
    statuses: list[PluginStatus] = []
    enabled_set = set(enabled)
    registered_names = set(existing_names)
    for name, entry in sorted(discovered.items()):
        if name not in enabled_set:
            statuses.append(PluginStatus(name, "discovered"))
            continue
        try:
            factory = cast(PluginFactory, entry.load())
            provided = list(factory(context))
            names: list[str] = []
            for tool in provided:
                _validate_definition(tool.definition)
                tool_name = tool.definition.name
                if tool_name in registered_names or tool_name in names:
                    raise ValueError(f"duplicate tool name: {tool_name}")
                names.append(tool_name)
                registered_names.add(tool_name)
                tools.append(PluginToolAdapter(tool, redactor, context.max_output_chars))
            if len(existing_names) + len(tools) > maximum_tools:
                raise ValueError(f"registered tool limit exceeds {maximum_tools}")
            statuses.append(PluginStatus(name, "loaded", tuple(names)))
        except Exception as exc:
            raise ConfigurationError(
                f"Could not load enabled plugin {name}: {type(exc).__name__}: "
                f"{redactor.redact(str(exc))}"
            ) from exc
    return tools, statuses


def _validate_definition(definition: ToolDefinition) -> None:
    if not isinstance(definition, ToolDefinition):
        raise ValueError("plugin tool definition has an invalid type")
    if not _TOOL_NAME.fullmatch(definition.name):
        raise ValueError("plugin tool name is invalid")
    if not definition.description.strip() or len(definition.description) > 500:
        raise ValueError("plugin tool description is invalid")
    schema = definition.parameters
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError("plugin tool schema must be a closed object")
