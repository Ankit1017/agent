"""Console implementation of explicit command approval."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

from local_harness.domain.models import ApprovalDecision, ProgressEvent
from local_harness.guardrails.redaction import SecretRedactor


class ConsoleApprovalGateway:
    """Render proposed commands and require an explicit affirmative answer."""

    def __init__(
        self,
        redactor: SecretRedactor,
        *,
        read: Callable[[str], str] = input,
        write: Callable[[str], None] = print,
    ) -> None:
        """Create a testable console interaction adapter."""
        self._redactor = redactor
        self._read = read
        self._write = write

    def request(self, command: str, explanation: str, workspace: str) -> ApprovalDecision:
        """Display risk context and default to rejection."""
        self._write("\nPowerShell approval required")
        self._write(f"Reason: {self._redactor.redact(explanation)}")
        self._write(f"Workspace: {workspace}")
        self._write(f"Command: {self._redactor.redact(command)}")
        self._write("Warning: approval is the security boundary; PowerShell is not OS-sandboxed.")
        answer = self._read("Execute this exact command? [y/N]: ").strip().casefold()
        if answer != "y":
            feedback = self._read("Optional rejection feedback: ").strip()
            return ApprovalDecision(False, self._redactor.redact(feedback))
        return ApprovalDecision(True)

    def request_patch(self, preview: str, explanation: str, workspace: str) -> ApprovalDecision:
        """Display an exact redacted diff and require explicit approval."""
        self._write("\nFile patch approval required")
        self._write(f"Reason: {self._redactor.redact(explanation)}")
        self._write(f"Workspace: {workspace}")
        self._write("Exact diff:")
        self._write(self._redactor.redact(preview))
        self._write("Warning: this patch will modify workspace files.")
        answer = self._read("Apply this exact patch? [y/N]: ").strip().casefold()
        if answer != "y":
            feedback = self._read("Optional rejection feedback: ").strip()
            return ApprovalDecision(False, self._redactor.redact(feedback))
        return ApprovalDecision(True)

    def request_maintenance(self, action: str, details: str) -> ApprovalDecision:
        """Require explicit confirmation for reversible session maintenance."""
        self._write(f"\n{self._redactor.redact(action)}")
        self._write(f"Target: {self._redactor.redact(details)}")
        answer = self._read("Continue? [y/N]: ").strip().casefold()
        return ApprovalDecision(answer == "y")


class ConsoleProgressSink:
    """Render compact lifecycle events while replacing interactive wait lines."""

    def __init__(self, redactor: SecretRedactor, stream: TextIO = sys.stdout) -> None:
        """Create a progress renderer for one output stream."""
        self._redactor = redactor
        self._stream = stream
        self._pending_wait = False

    def publish(self, event: ProgressEvent) -> None:
        """Render one redacted progress event."""
        line = _encodable(format_progress_event(event, self._redactor), self._stream)
        interactive = self._stream.isatty()
        if event.kind == "model_start" and interactive:
            self._stream.write(line)
            self._stream.flush()
            self._pending_wait = True
            return
        if self._pending_wait:
            self._stream.write(f"\r\x1b[2K{line}\n")
            self._pending_wait = False
        else:
            self._stream.write(f"{line}\n")
        self._stream.flush()


def format_progress_event(event: ProgressEvent, redactor: SecretRedactor) -> str:
    """Format a stored event as one terminal-safe line."""
    summary = redactor.redact(event.summary)
    target = redactor.redact(event.target)
    if event.kind == "model_start":
        return f"[LLM #{event.call_number}] {summary}..."
    seconds = event.duration_ms / 1000
    marker = {"success": "OK", "warning": "WARN", "error": "ERROR"}.get(event.status, "RUNNING")
    if event.kind.startswith("model_"):
        tokens = event.input_tokens + event.output_tokens
        usage = f" | {tokens} tokens" if tokens else ""
        return (
            f"[{marker}] LLM #{event.call_number} | {seconds:.1f}s{usage} | {summary} -> {target}"
        )
    if event.kind.startswith("workflow_"):
        return f"[{marker}] WORKFLOW | {summary} -> {target}"
    if not event.kind.startswith("tool_"):
        return f"[{marker}] SYSTEM | {summary} -> {target}"
    return f"[{marker}] TOOL | {seconds:.1f}s | {target} - {summary}"


def _encodable(value: str, stream: TextIO) -> str:
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return value
    return value.encode(encoding, errors="replace").decode(encoding)
