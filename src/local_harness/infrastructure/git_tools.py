"""Read-only Git inspection with workspace and output boundaries."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence

from local_harness.domain.errors import HarnessError, ToolExecutionError
from local_harness.domain.models import ToolDefinition, ToolResult
from local_harness.guardrails.path_policy import WorkspacePathPolicy
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.tool_output import tool_envelope


class GitInspectTool:
    """Expose bounded status, diff, history, and blame without Git mutations."""

    def __init__(
        self,
        policy: WorkspacePathPolicy,
        redactor: SecretRedactor,
        *,
        max_output_chars: int,
    ) -> None:
        """Configure workspace validation, redaction, and output limits."""
        self._policy = policy
        self._redactor = redactor
        self._max_output_chars = max_output_chars

    @property
    def definition(self) -> ToolDefinition:
        """Return the closed read-only Git schema."""
        return ToolDefinition(
            "git_inspect",
            "Review Git changes, repository status, diffs, history, or blame without mutation.",
            {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["overview", "diff", "history", "blame"],
                    },
                    "paths": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {"type": "string"},
                    },
                    "base": {"type": "string", "default": "HEAD"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["operation"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Run one fixed read-only Git operation using an argument array."""
        try:
            operation = arguments.get("operation")
            if operation not in {"overview", "diff", "history", "blame"}:
                raise ToolExecutionError("operation must be overview, diff, history, or blame")
            paths = self._paths(arguments.get("paths", []))
            limit = arguments.get("limit", 20)
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
                raise ToolExecutionError("limit must be an integer between 1 and 50")
            if operation == "overview":
                return self._overview(limit)
            if operation == "diff":
                base = arguments.get("base", "HEAD")
                if not isinstance(base, str) or not base.strip() or base.startswith("-"):
                    raise ToolExecutionError("base must be a non-option revision")
                selected = paths or self._changed_paths()
                output = self._git(["diff", "--no-ext-diff", "--unified=3", base, "--", *selected])
                return self._result("Git diff inspected", [{"diff": output, "paths": selected}])
            if operation == "history":
                args = [
                    "log",
                    f"-{limit}",
                    "--date=iso-strict",
                    "--pretty=format:%h%x09%ad%x09%s",
                ]
                if paths:
                    args.extend(["--", *paths])
                return self._result("Git history inspected", [{"history": self._git(args)}])
            if len(paths) != 1:
                raise ToolExecutionError("blame requires exactly one workspace-relative path")
            output = self._git(["blame", "--line-porcelain", "--", paths[0]])
            return self._result("Git blame inspected", [{"path": paths[0], "blame": output}])
        except (OSError, subprocess.SubprocessError, HarnessError) as exc:
            return ToolResult(self._redactor.redact(str(exc)), True)

    def _overview(self, limit: int) -> ToolResult:
        status = self._git(["status", "--porcelain=v1", "--branch", "--untracked-files=all"])
        lines = status.splitlines()
        branch = lines[0][3:] if lines and lines[0].startswith("## ") else "unknown"
        changed = self._changed_paths(lines[1:] if branch != "unknown" else lines)
        try:
            history = self._git(["log", f"-{min(limit, 10)}", "--pretty=format:%h%x09%s"])
        except ToolExecutionError as exc:
            if "does not have any commits yet" not in str(exc):
                raise
            history = ""
        return self._result(
            f"Repository has {len(changed)} visible changed path(s)",
            [
                {
                    "branch": branch,
                    "clean": not changed,
                    "changed_paths": changed,
                    "history": history,
                }
            ],
        )

    def _changed_paths(self, status_lines: Sequence[str] | None = None) -> list[str]:
        lines = (
            list(status_lines)
            if status_lines is not None
            else self._git(["status", "--porcelain=v1", "--untracked-files=all"]).splitlines()
        )
        paths: list[str] = []
        for line in lines:
            raw = line[3:] if len(line) > 3 else ""
            raw = raw.split(" -> ")[-1].strip().strip('"').replace("\\", "/")
            try:
                path = self._policy.resolve(raw, allow_root=False)
            except (OSError, HarnessError):
                continue
            if not self._policy.is_protected(path):
                paths.append(str(path.relative_to(self._policy.workspace)).replace("\\", "/"))
        return sorted(set(paths), key=str.casefold)

    def _paths(self, value: object) -> list[str]:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ToolExecutionError("paths must be an array of strings")
        selected: list[str] = []
        for raw in value:
            path = self._policy.resolve(raw, allow_root=False)
            if self._policy.is_protected(path):
                raise ToolExecutionError(f"Protected Git path: {raw}")
            selected.append(str(path.relative_to(self._policy.workspace)).replace("\\", "/"))
        return selected

    def _git(self, arguments: list[str]) -> str:
        command = ["git", "--no-pager", "-C", str(self._policy.workspace), *arguments]
        environment = os.environ.copy()
        environment.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat"})
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            env=environment,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or "Git inspection failed"
            raise ToolExecutionError(message[:1_000])
        return completed.stdout

    def _result(self, summary: str, items: list[dict[str, object]]) -> ToolResult:
        return ToolResult(
            tool_envelope(
                summary,
                items,
                max_chars=self._max_output_chars,
                redactor=self._redactor,
            )
        )
