"""Registry for independently implemented model tools."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from local_harness.application.ports import Tool
from local_harness.domain.errors import ToolExecutionError


class ToolRegistry:
    """Resolve tools by unique name and expose their schemas."""

    def __init__(self, tools: list[Tool]) -> None:
        """Create a registry and reject duplicate names."""
        self._tools = {tool.definition.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ValueError("Tool names must be unique")

    @property
    def tools(self) -> list[Tool]:
        """Return tools in stable registration order."""
        return list(self._tools.values())

    def get(self, name: str) -> Tool:
        """Return a named tool or raise a safe execution error."""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolExecutionError(f"Unknown tool: {name}") from exc

    def with_tools(self, tools: list[Tool]) -> ToolRegistry:
        """Return a new registry containing existing tools plus additional adapters."""
        return ToolRegistry([*self.tools, *tools])

    def restricted_to(self, names: Sequence[str]) -> ToolRegistry:
        """Return a registry containing only exact allowlisted registered tools."""
        allowed = frozenset(names)
        return ToolRegistry(
            [tool for tool in self._tools.values() if tool.definition.name in allowed]
        )

    def resolve_placeholder(self, name: str, arguments: Mapping[str, object]) -> str:
        """Infer a placeholder tool name only when one closed schema matches uniquely."""
        if name in self._tools:
            return name
        if name.strip().casefold() not in {"", "?", "tool", "unknown"}:
            return name
        argument_names = set(arguments) - {"step_summary"}
        matches: list[str] = []
        for tool in self._tools.values():
            schema = tool.definition.parameters
            properties = schema.get("properties")
            required = schema.get("required", [])
            if not isinstance(properties, dict) or not isinstance(required, list):
                continue
            property_names = set(properties) - {"step_summary"}
            required_names = {
                item for item in required if isinstance(item, str) and item != "step_summary"
            }
            if required_names <= argument_names <= property_names:
                matches.append(tool.definition.name)
        return matches[0] if len(matches) == 1 else name
