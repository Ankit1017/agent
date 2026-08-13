"""Bounded read-only tools over the workspace project-memory index."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Literal, cast

from local_harness.application.ports import ProjectIndexRepository
from local_harness.domain.errors import HarnessError
from local_harness.domain.models import ToolDefinition, ToolResult
from local_harness.domain.project_memory import ProjectMemoryQuery
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.tool_output import tool_envelope


class ProjectMemoryTool:
    """Retrieve ranked architecture, symbol, documentation, or dependency facts."""

    def __init__(
        self,
        index: ProjectIndexRepository,
        redactor: SecretRedactor,
        *,
        max_output_chars: int,
    ) -> None:
        """Configure the project-memory retrieval tool."""
        self._index = index
        self._redactor = redactor
        self._max_output_chars = max_output_chars

    @property
    def definition(self) -> ToolDefinition:
        """Return the closed model-facing schema."""
        return ToolDefinition(
            "project_memory",
            "Retrieve compact ranked facts from the reusable local project index.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                    "category": {
                        "type": "string",
                        "enum": ["architecture", "symbol", "documentation", "dependency", "all"],
                        "default": "all",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 12,
                        "default": 8,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Return a bounded hybrid retrieval result."""
        try:
            query = _string(arguments, "query")
            category = _string(arguments, "category", "all")
            if category not in {"architecture", "symbol", "documentation", "dependency", "all"}:
                return ToolResult("category is invalid", True)
            category_value = cast(
                Literal["architecture", "symbol", "documentation", "dependency", "all"],
                category,
            )
            result = self._index.retrieve(
                ProjectMemoryQuery(
                    query,
                    category=category_value,
                    max_results=_integer(arguments, "max_results", 8),
                )
            )
            return ToolResult(
                tool_envelope(
                    f"Retrieved {len(result.hits)} project-memory source(s)",
                    [asdict(item) for item in result.hits],
                    max_chars=self._max_output_chars,
                    redactor=self._redactor,
                    metadata={
                        "generation": result.generation,
                        "retrieval_mode": result.retrieval_mode,
                        "candidate_chars": result.candidate_chars,
                        "injected_chars": result.injected_chars,
                        "warning": result.warning,
                    },
                )
            )
        except (HarnessError, ValueError) as exc:
            return ToolResult(str(exc), True)


class ReadSymbolTool:
    """Read exactly one indexed symbol using its stable identifier."""

    def __init__(
        self, index: ProjectIndexRepository, redactor: SecretRedactor, *, max_output_chars: int
    ) -> None:
        """Configure exact symbol reading."""
        self._index = index
        self._redactor = redactor
        self._max_output_chars = max_output_chars

    @property
    def definition(self) -> ToolDefinition:
        """Return the closed model-facing schema."""
        return ToolDefinition(
            "read_symbol",
            "Read one current indexed function, class, or declaration by stable source ID.",
            {
                "type": "object",
                "properties": {"symbol_id": {"type": "string", "minLength": 1}},
                "required": ["symbol_id"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Read one symbol or report a stale/unknown identifier."""
        try:
            item = self._index.read_symbol(_string(arguments, "symbol_id"))
            return ToolResult(
                tool_envelope(
                    f"Read symbol {item['name']}",
                    [item],
                    max_chars=self._max_output_chars,
                    redactor=self._redactor,
                )
            )
        except (HarnessError, OSError, UnicodeDecodeError, ValueError) as exc:
            return ToolResult(str(exc), True)


class ChangedContextTool:
    """Expose the latest indexed file/symbol delta and read-only Git state."""

    def __init__(
        self, index: ProjectIndexRepository, redactor: SecretRedactor, *, max_output_chars: int
    ) -> None:
        """Configure changed-context output."""
        self._index = index
        self._redactor = redactor
        self._max_output_chars = max_output_chars

    @property
    def definition(self) -> ToolDefinition:
        """Return the closed model-facing schema."""
        return ToolDefinition(
            "changed_context",
            "Return indexed file/symbol changes, Git status, and focused-check context.",
            {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}
                },
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Return the latest bounded delta."""
        try:
            delta = self._index.changed_context(_integer(arguments, "limit", 50))
            item = asdict(delta)
            recommendations = _recommended_checks((*delta.created, *delta.modified))
            item["recommended_checks"] = recommendations
            return ToolResult(
                tool_envelope(
                    "Loaded changed project context",
                    [item],
                    max_chars=self._max_output_chars,
                    redactor=self._redactor,
                )
            )
        except (HarnessError, ValueError) as exc:
            return ToolResult(str(exc), True)


class DependencyContextTool:
    """Query dependency facts parsed from safe project manifests."""

    def __init__(
        self, index: ProjectIndexRepository, redactor: SecretRedactor, *, max_output_chars: int
    ) -> None:
        """Configure dependency lookup output."""
        self._index = index
        self._redactor = redactor
        self._max_output_chars = max_output_chars

    @property
    def definition(self) -> ToolDefinition:
        """Return the closed model-facing schema."""
        return ToolDefinition(
            "dependency_context",
            "Query locally parsed dependency and manifest facts without executing project code.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "default": ""},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                },
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Return bounded parsed dependency facts."""
        try:
            values = self._index.dependencies(
                _string(arguments, "query", ""), _integer(arguments, "limit", 50)
            )
            return ToolResult(
                tool_envelope(
                    f"Found {len(values)} dependency fact(s)",
                    [asdict(item) for item in values],
                    max_chars=self._max_output_chars,
                    redactor=self._redactor,
                )
            )
        except (HarnessError, ValueError) as exc:
            return ToolResult(str(exc), True)


def _string(arguments: Mapping[str, object], name: str, default: str = "") -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _integer(arguments: Mapping[str, object], name: str, default: int) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _recommended_checks(paths: tuple[str, ...]) -> list[str]:
    checks: set[str] = set()
    for path in paths:
        suffix = path.rsplit(".", 1)[-1].casefold() if "." in path else ""
        if suffix == "py":
            checks.update(("tests", "lint", "type-check"))
        elif suffix in {"js", "jsx", "ts", "tsx"}:
            checks.update(("tests", "lint", "build-check"))
        else:
            checks.add("focused project check")
    return sorted(checks)
