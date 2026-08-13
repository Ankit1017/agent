"""Bounded code-intelligence tool with deterministic syntax fallback."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from typing import Literal

from local_harness.domain.errors import ToolExecutionError
from local_harness.domain.models import ToolDefinition, ToolResult
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.code_search import CodeFinder
from local_harness.infrastructure.tool_output import tool_envelope


class CodeIntelligenceTool:
    """Navigate symbols and report language-server availability."""

    def __init__(
        self,
        finder: CodeFinder,
        redactor: SecretRedactor,
        *,
        max_output_chars: int,
        python_command: str = "",
        typescript_command: str = "",
    ) -> None:
        """Configure syntax fallback and optional language-server commands."""
        self._finder = finder
        self._redactor = redactor
        self._max_output_chars = max_output_chars
        self._python_command = python_command or _first_command(
            "basedpyright-langserver", "pyright-langserver"
        )
        self._typescript_command = typescript_command or _first_command(
            "typescript-language-server"
        )

    @property
    def definition(self) -> ToolDefinition:
        """Return the closed code-intelligence operation schema."""
        return ToolDefinition(
            "code_intelligence",
            "Find definitions, references, symbols, hover context, or diagnostics availability.",
            {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "definition",
                            "references",
                            "hover",
                            "document_symbols",
                            "diagnostics",
                        ],
                    },
                    "query": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "language": {
                        "type": "string",
                        "enum": ["python", "javascript", "typescript", "tsx"],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["operation", "path", "language"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Return bounded navigation results or explicit diagnostic availability."""
        try:
            operation = arguments.get("operation")
            path, language = arguments.get("path"), arguments.get("language")
            query, limit = arguments.get("query", ""), arguments.get("limit", 20)
            if operation not in {
                "definition",
                "references",
                "hover",
                "document_symbols",
                "diagnostics",
            }:
                raise ToolExecutionError("Unknown code-intelligence operation")
            if not isinstance(path, str) or not isinstance(language, str):
                raise ToolExecutionError("path and language must be strings")
            if not isinstance(query, str) or not isinstance(limit, int) or isinstance(limit, bool):
                raise ToolExecutionError("query and limit are invalid")
            server = self._server(language)
            if operation == "diagnostics":
                diagnostic_item: dict[str, object] = {
                    "language": language,
                    "server_command": server or None,
                    "available": bool(server),
                    "diagnostics": [],
                    "note": (
                        "Language server detected; use run_project_checks for "
                        "authoritative diagnostics"
                        if server
                        else "No language server detected; no installation was attempted"
                    ),
                }
                return self._result("Diagnostics capability inspected", [diagnostic_item])
            if operation == "document_symbols" and not query.strip():
                query = "class"
            if not query.strip():
                raise ToolExecutionError(f"{operation} requires query")
            kind: Literal["definition", "reference"] = (
                "definition" if operation in {"definition", "document_symbols"} else "reference"
            )
            items, truncated, cursor = self._finder.find(
                query, path, kind, [language], min(max(limit, 1), 50), None
            )
            if operation == "hover":
                items = items[:1]
            for item in items:
                item["provider"] = "tree-sitter-fallback"
                item["language_server"] = server or None
            return ToolResult(
                tool_envelope(
                    f"Found {len(items)} {operation} result(s)",
                    items,
                    max_chars=self._max_output_chars,
                    redactor=self._redactor,
                    truncated=truncated,
                    next_cursor=cursor,
                )
            )
        except (OSError, RuntimeError, ToolExecutionError) as exc:
            return ToolResult(self._redactor.redact(str(exc)), True)

    def availability(self) -> dict[str, str | None]:
        """Return detected language-server commands without starting processes."""
        return {
            "python": self._python_command or None,
            "typescript": self._typescript_command or None,
        }

    def _server(self, language: str) -> str:
        return self._python_command if language == "python" else self._typescript_command

    def _result(self, summary: str, items: list[dict[str, object]]) -> ToolResult:
        return ToolResult(
            tool_envelope(
                summary,
                items,
                max_chars=self._max_output_chars,
                redactor=self._redactor,
            )
        )


def _first_command(*names: str) -> str:
    return next((name for name in names if shutil.which(name)), "")
