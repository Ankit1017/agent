"""Tests for the bounded PowerShell process adapter."""

from __future__ import annotations

from pathlib import Path

from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.powershell import PowerShellExecutor


def test_powershell_captures_exit_output_and_redacts(tmp_path: Path) -> None:
    """Process output is bounded, redacted, and reports nonzero exits."""
    executor = PowerShellExecutor(
        tmp_path,
        timeout_seconds=5,
        max_output_chars=20,
        redactor=SecretRedactor(("secret",)),
    )

    success = executor.execute("Write-Output 'secret and a long output value'")
    failure = executor.execute("Write-Error 'failed'; exit 7")

    assert success.status == "completed"
    assert "secret" not in success.stdout
    assert success.truncated
    assert failure.status == "failed"
    assert failure.exit_code == 7


def test_powershell_reports_timeout(tmp_path: Path) -> None:
    """Commands exceeding the configured limit return a timeout result."""
    executor = PowerShellExecutor(
        tmp_path,
        timeout_seconds=1,
        max_output_chars=100,
        redactor=SecretRedactor(),
    )

    result = executor.execute("Start-Sleep -Seconds 3")

    assert result.status == "timed_out"
    assert result.timed_out
