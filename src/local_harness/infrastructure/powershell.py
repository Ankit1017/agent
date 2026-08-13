"""Non-interactive PowerShell command executor."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Literal

from local_harness.domain.errors import ToolExecutionError
from local_harness.domain.models import CommandExecution
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.text import truncate_text


class PowerShellExecutor:
    """Execute approved PowerShell commands in the launch workspace."""

    def __init__(
        self,
        workspace: Path,
        *,
        timeout_seconds: int,
        max_output_chars: int,
        redactor: SecretRedactor,
    ) -> None:
        """Configure the command process and output boundary."""
        self._workspace = workspace
        self._timeout_seconds = timeout_seconds
        self._max_output_chars = max_output_chars
        self._redactor = redactor
        executable = shutil.which("powershell.exe") or shutil.which("powershell")
        if executable is None:
            raise ToolExecutionError("Windows PowerShell executable was not found")
        self._executable = executable

    def execute(self, command: str) -> CommandExecution:
        """Run one command without an interactive profile or stdin."""
        try:
            completed = subprocess.run(
                [self._executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=self._workspace,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            partial = _timeout_output(exc)
            bounded, truncated = truncate_text(
                self._redactor.redact(partial), self._max_output_chars
            )
            return CommandExecution("timed_out", None, bounded, True, truncated)
        output = completed.stdout
        if completed.stderr:
            output = f"{output}\n{completed.stderr}" if output else completed.stderr
        bounded, truncated = truncate_text(
            self._redactor.redact(output.strip() or "(no output)"), self._max_output_chars
        )
        status: Literal["completed", "failed"] = (
            "completed" if completed.returncode == 0 else "failed"
        )
        return CommandExecution(status, completed.returncode, bounded, False, truncated)


def _timeout_output(exc: subprocess.TimeoutExpired) -> str:
    output = exc.stdout or ""
    error = exc.stderr or ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    if isinstance(error, bytes):
        error = error.decode("utf-8", errors="replace")
    partial = f"{output}\n{error}".strip()
    return partial or "Command timed out before producing output"
