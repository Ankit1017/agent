"""Tests for human command approval interaction."""

from __future__ import annotations

from io import StringIO

from local_harness.domain.models import ProgressEvent
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.interfaces.console import (
    ConsoleApprovalGateway,
    ConsoleProgressSink,
    format_progress_event,
)


def test_console_approval_defaults_to_no_and_collects_feedback() -> None:
    """Only an exact y approves; rejected feedback is returned safely."""
    answers = iter(["", "contains sk-abcdefghijklmnop"])
    output: list[str] = []
    gateway = ConsoleApprovalGateway(
        SecretRedactor(), read=lambda _: next(answers), write=output.append
    )

    decision = gateway.request("Get-Date", "reason", "workspace")

    assert not decision.approved
    assert decision.feedback == "contains [REDACTED]"
    assert any("not OS-sandboxed" in line for line in output)


def test_console_approval_accepts_lowercase_y_only() -> None:
    """An affirmative response approves the exact displayed command."""
    gateway = ConsoleApprovalGateway(SecretRedactor(), read=lambda _: "Y", write=lambda _: None)

    assert gateway.request("Get-Date", "reason", "workspace").approved


def test_console_patch_approval_shows_redacted_diff_and_defaults_to_no() -> None:
    """Patch approval displays the exact safe preview and collects rejection feedback."""
    answers = iter(["n", "change approach"])
    output: list[str] = []
    gateway = ConsoleApprovalGateway(
        SecretRedactor(("secret-value",)),
        read=lambda _: next(answers),
        write=output.append,
    )

    decision = gateway.request_patch("+ secret-value", "Edit file", "workspace")

    assert decision.feedback == "change approach"
    assert not decision.approved
    assert "[REDACTED]" in "\n".join(output)


class TtyBuffer(StringIO):
    """String buffer that behaves like an interactive terminal."""

    def isatty(self) -> bool:
        """Report interactive output support."""
        return True


def test_progress_sink_replaces_interactive_wait_line() -> None:
    """A completed model event replaces its pending wait status on a TTY."""
    stream = TtyBuffer()
    sink = ConsoleProgressSink(SecretRedactor(), stream)
    sink.publish(ProgressEvent(1, 2, "model_start", "Waiting", "model", "started"))
    sink.publish(
        ProgressEvent(2, 2, "model_complete", "Inspecting files", "read_file", "success", 1250)
    )

    output = stream.getvalue()
    assert "\r\x1b[2K" in output
    assert "[OK] LLM #2 | 1.2s | Inspecting files -> read_file" in output


def test_progress_format_and_noninteractive_output_are_compact() -> None:
    """Redirected model and tool events remain readable one-line records."""
    stream = StringIO()
    sink = ConsoleProgressSink(SecretRedactor(("secret",)), stream)
    start = ProgressEvent(1, 1, "model_start", "Waiting for secret", "secret", "started")
    tool = ProgressEvent(2, 1, "tool_error", "Reading file", "read_file", "error", 50)
    sink.publish(start)
    sink.publish(tool)

    assert "[REDACTED]" in stream.getvalue()
    assert "[ERROR] TOOL | 0.1s | read_file - Reading file" in stream.getvalue()
    assert "-> final" in format_progress_event(
        ProgressEvent(3, 2, "model_complete", "Done", "final", "success", 100),
        SecretRedactor(),
    )
