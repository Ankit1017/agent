"""Model tools backed by guarded infrastructure services."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict

from local_harness.application.ports import ApprovalGateway, CommandExecutor
from local_harness.domain.errors import HarnessError, ToolExecutionError
from local_harness.domain.models import ToolDefinition, ToolResult
from local_harness.guardrails.command_policy import evaluate_command
from local_harness.infrastructure.filesystem import WorkspaceInspector


class ListDirectoryTool:
    """Expose bounded directory listing to the model."""

    def __init__(self, inspector: WorkspaceInspector) -> None:
        self._inspector = inspector

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool's JSON schema."""
        return ToolDefinition(
            "list_directory",
            "List visible files and folders in one workspace directory. This runs automatically.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path"}
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """List a validated path."""
        return _safe_result(lambda: self._inspector.list_directory(_string(arguments, "path")))


class ReadFileTool:
    """Expose bounded UTF-8 file reading to the model."""

    def __init__(self, inspector: WorkspaceInspector) -> None:
        self._inspector = inspector

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool's JSON schema."""
        return ToolDefinition(
            "read_file",
            "Read an inclusive line range from a UTF-8 workspace file. This runs automatically.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1, "default": 1},
                    "end_line": {"type": "integer", "minimum": 1, "default": 400},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Read a validated line range."""
        return _safe_result(
            lambda: self._inspector.read_file(
                _string(arguments, "path"),
                _integer(arguments, "start_line", 1),
                _integer(arguments, "end_line", 400),
            )
        )


class SearchTextTool:
    """Expose bounded literal text search to the model."""

    def __init__(self, inspector: WorkspaceInspector) -> None:
        self._inspector = inspector

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool's JSON schema."""
        return ToolDefinition(
            "search_text",
            "Search visible UTF-8 workspace files for literal text. This runs automatically.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "file_pattern": {"type": "string", "default": "*"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Search a validated directory."""
        return _safe_result(
            lambda: self._inspector.search_text(
                _string(arguments, "query"),
                _string(arguments, "path", "."),
                _string(arguments, "file_pattern", "*"),
            )
        )


class RunPowerShellTool:
    """Apply command policy and human approval before process execution."""

    def __init__(
        self, executor: CommandExecutor, approval: ApprovalGateway, workspace: str
    ) -> None:
        self._executor = executor
        self._approval = approval
        self._workspace = workspace

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool's JSON schema."""
        return ToolDefinition(
            "run_powershell",
            "Propose one non-interactive PowerShell command. Every call requires human approval.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "explanation": {
                        "type": "string",
                        "description": "What the command does and why it is needed",
                    },
                },
                "required": ["command", "explanation"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Block, request approval for, and optionally execute a command."""
        try:
            command = _string(arguments, "command")
            explanation = _string(arguments, "explanation")
        except ToolExecutionError as exc:
            return ToolResult(str(exc), True)
        policy = evaluate_command(command)
        if not policy.allowed:
            return ToolResult(f"Blocked by command guardrail: {policy.reason}", True)
        decision = self._approval.request(command, explanation, self._workspace)
        if not decision.approved:
            feedback = decision.feedback or "No reason supplied"
            return ToolResult(f"User rejected the command. Feedback: {feedback}", True)
        execution = self._executor.execute(command)
        payload = {"approval": "approved", **asdict(execution)}
        return ToolResult(json.dumps(payload, ensure_ascii=False), execution.status != "completed")


def _safe_result(operation: object) -> ToolResult:
    if not callable(operation):
        return ToolResult("Internal tool configuration error", True)
    try:
        return ToolResult(operation())
    except (HarnessError, OSError) as exc:
        return ToolResult(str(exc), True)


def _string(arguments: Mapping[str, object], name: str, default: str | None = None) -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str):
        raise ToolExecutionError(f"{name} must be a string")
    return value


def _integer(arguments: Mapping[str, object], name: str, default: int) -> int:
    value = arguments.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolExecutionError(f"{name} must be an integer")
    return value
