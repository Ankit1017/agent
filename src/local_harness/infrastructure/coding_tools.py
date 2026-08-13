"""High-information coding tools with compact structured outputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import cast

from local_harness.application.ports import ApprovalGateway, CommandExecutor, ProjectIndexRepository
from local_harness.domain.errors import HarnessError, ToolExecutionError
from local_harness.domain.models import ToolDefinition, ToolResult
from local_harness.guardrails.command_policy import evaluate_command
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.code_search import CodeFinder, SymbolKind
from local_harness.infrastructure.patching import WorkspacePatchService
from local_harness.infrastructure.project_inspection import (
    BatchFileReader,
    CheckProfileDetector,
    ProjectInspector,
)
from local_harness.infrastructure.tool_output import tool_envelope


class InspectProjectTool:
    """Expose compact project structure and detected verification profiles."""

    def __init__(
        self,
        inspector: ProjectInspector,
        redactor: SecretRedactor,
        *,
        max_output_chars: int,
        project_memory: ProjectIndexRepository | None = None,
    ) -> None:
        """Configure project inspection output."""
        self._inspector = inspector
        self._redactor = redactor
        self._max_output_chars = max_output_chars
        self._project_memory = project_memory

    @property
    def definition(self) -> ToolDefinition:
        """Return the model-facing project inspection schema."""
        return ToolDefinition(
            "inspect_project",
            "Summarize project structure, Git, language servers, and check profiles.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
                },
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Inspect a guarded project root."""
        try:
            result = self._inspector.inspect(
                _string(arguments, "path", "."), _integer(arguments, "depth", 3)
            )
            truncated = bool(result.pop("truncated"))
            if self._project_memory is not None:
                result["project_memory"] = asdict(self._project_memory.status())
            return ToolResult(
                tool_envelope(
                    "Inspected project structure",
                    [result],
                    max_chars=self._max_output_chars,
                    redactor=self._redactor,
                    truncated=truncated,
                    metadata={"depth": _integer(arguments, "depth", 3)},
                )
            )
        except (HarnessError, OSError) as exc:
            return ToolResult(str(exc), True)


class ReadFilesTool:
    """Expose several guarded file ranges in one model call."""

    def __init__(
        self,
        reader: BatchFileReader,
        redactor: SecretRedactor,
        *,
        max_files: int,
        max_output_chars: int,
    ) -> None:
        """Configure batch and output limits."""
        self._reader = reader
        self._redactor = redactor
        self._max_files = max_files
        self._max_output_chars = max_output_chars

    @property
    def definition(self) -> ToolDefinition:
        """Return the model-facing batch-read schema."""
        return ToolDefinition(
            "read_files",
            "Read up to eight targeted UTF-8 file ranges in one call.",
            {
                "type": "object",
                "properties": {
                    "requests": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": self._max_files,
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "start_line": {"type": "integer", "minimum": 1, "default": 1},
                                "end_line": {"type": "integer", "minimum": 1, "default": 200},
                            },
                            "required": ["path"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["requests"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Read independent ranges while retaining per-file errors."""
        raw_requests = arguments.get("requests")
        if not isinstance(raw_requests, list) or not 1 <= len(raw_requests) <= self._max_files:
            return ToolResult(f"requests must contain 1 to {self._max_files} items", True)
        items: list[dict[str, object]] = []
        failures = 0
        for raw in raw_requests:
            if not isinstance(raw, dict):
                items.append({"status": "error", "error": "request must be an object"})
                failures += 1
                continue
            try:
                item = self._reader.read(
                    _string(raw, "path"),
                    _integer(raw, "start_line", 1),
                    _integer(raw, "end_line", 200),
                )
                item["status"] = "success"
                items.append(item)
            except (HarnessError, OSError) as exc:
                failures += 1
                items.append(
                    {
                        "path": raw.get("path", "(invalid)"),
                        "status": "error",
                        "error": str(exc),
                    }
                )
        return ToolResult(
            tool_envelope(
                f"Read {len(items) - failures} of {len(items)} file range(s)",
                items,
                max_chars=self._max_output_chars,
                redactor=self._redactor,
                metadata={"failures": failures},
            ),
            failures == len(items),
        )


class FindCodeTool:
    """Expose Tree-sitter-backed syntactic source search."""

    def __init__(
        self, finder: CodeFinder, redactor: SecretRedactor, *, max_output_chars: int
    ) -> None:
        """Configure code search output."""
        self._finder = finder
        self._redactor = redactor
        self._max_output_chars = max_output_chars

    @property
    def definition(self) -> ToolDefinition:
        """Return the model-facing code-search schema."""
        return ToolDefinition(
            "find_code",
            "Find syntactic definitions, imports, or identifier references across source files.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "kind": {
                        "type": "string",
                        "enum": ["any", "definition", "import", "reference"],
                        "default": "any",
                    },
                    "languages": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                    "cursor": {"type": "string"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Search source files and return one bounded result page."""
        try:
            raw_languages = arguments.get("languages", [])
            if not isinstance(raw_languages, list) or not all(
                isinstance(item, str) for item in raw_languages
            ):
                raise ToolExecutionError("languages must be an array of strings")
            raw_kind = _string(arguments, "kind", "any")
            items, truncated, next_cursor = self._finder.find(
                _string(arguments, "query"),
                _string(arguments, "path", "."),
                cast(SymbolKind, raw_kind),
                raw_languages,
                _integer(arguments, "limit", 50),
                _optional_string(arguments, "cursor"),
            )
            return ToolResult(
                tool_envelope(
                    f"Found {len(items)} code match(es)",
                    items,
                    max_chars=self._max_output_chars,
                    redactor=self._redactor,
                    truncated=truncated,
                    next_cursor=next_cursor,
                )
            )
        except (HarnessError, OSError) as exc:
            return ToolResult(str(exc), True)


class ApplyPatchTool:
    """Expose explicitly approved transactional workspace changes."""

    def __init__(self, service: WorkspacePatchService) -> None:
        """Bind the model tool to its guarded patch service."""
        self._service = service

    @property
    def definition(self) -> ToolDefinition:
        """Return the model-facing structured patch schema."""
        operation = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "replace", "delete"]},
                "path": {"type": "string"},
                "content": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "expected_sha256": {"type": "string"},
            },
            "required": ["action", "path"],
            "additionalProperties": False,
        }
        return ToolDefinition(
            "apply_patch",
            "Preview and request approval for atomic create, exact-replace, or delete changes.",
            {
                "type": "object",
                "properties": {
                    "changes": {"type": "array", "minItems": 1, "maxItems": 20, "items": operation},
                    "explanation": {"type": "string"},
                },
                "required": ["changes", "explanation"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Validate and apply an approved patch request."""
        changes = arguments.get("changes")
        if not isinstance(changes, list) or not all(isinstance(item, dict) for item in changes):
            return ToolResult("changes must be an array of objects", True)
        try:
            explanation = _string(arguments, "explanation")
        except ToolExecutionError as exc:
            return ToolResult(str(exc), True)
        return self._service.apply(changes, explanation)


class RunProjectChecksTool:
    """Run one detected, policy-checked, explicitly approved project check."""

    def __init__(
        self,
        detector: CheckProfileDetector,
        executor: CommandExecutor,
        approval: ApprovalGateway,
        workspace: str,
        redactor: SecretRedactor,
        *,
        max_output_chars: int,
    ) -> None:
        """Configure detection, execution, approval, and output boundaries."""
        self._detector = detector
        self._executor = executor
        self._approval = approval
        self._workspace = workspace
        self._redactor = redactor
        self._max_output_chars = max_output_chars

    @property
    def definition(self) -> ToolDefinition:
        """Return the model-facing project-check schema."""
        return ToolDefinition(
            "run_project_checks",
            "Run one check profile previously returned by inspect_project; approval is required.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "profile": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["profile", "explanation"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Resolve and execute one known profile after exact approval."""
        try:
            path = _string(arguments, "path", ".")
            profile = _string(arguments, "profile")
            explanation = _string(arguments, "explanation")
            profiles = self._detector.detect(path)
            if profile not in profiles:
                raise ToolExecutionError(f"Unknown check profile: {profile}")
            command = profiles[profile]
            policy = evaluate_command(command)
            if not policy.allowed:
                raise ToolExecutionError(f"Blocked by command guardrail: {policy.reason}")
            decision = self._approval.request(command, explanation, self._workspace)
            if not decision.approved:
                feedback = decision.feedback or "No reason supplied"
                return ToolResult(f"User rejected the check. Feedback: {feedback}", True)
            execution = self._executor.execute(command)
            item = {
                "profile": profile,
                "status": execution.status,
                "exit_code": execution.exit_code,
                "output": execution.stdout,
                "timed_out": execution.timed_out,
            }
            return ToolResult(
                tool_envelope(
                    f"Check {profile} {execution.status}",
                    [item],
                    max_chars=self._max_output_chars,
                    redactor=self._redactor,
                    truncated=execution.truncated,
                ),
                execution.status != "completed",
            )
        except (HarnessError, OSError) as exc:
            return ToolResult(str(exc), True)


def _string(arguments: Mapping[str, object], name: str, default: str | None = None) -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str):
        raise ToolExecutionError(f"{name} must be a string")
    return value


def _optional_string(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolExecutionError(f"{name} must be a string")
    return value


def _integer(arguments: Mapping[str, object], name: str, default: int) -> int:
    value = arguments.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolExecutionError(f"{name} must be an integer")
    return value
