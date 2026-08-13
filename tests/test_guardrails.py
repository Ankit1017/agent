"""Tests for code-enforced safety policies."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_harness.domain.errors import PolicyViolation
from local_harness.guardrails.command_policy import evaluate_command
from local_harness.guardrails.path_policy import WorkspacePathPolicy
from local_harness.guardrails.redaction import SecretRedactor


@pytest.mark.parametrize(
    "command",
    [
        "Format-Volume -DriveLetter C",
        "Clear-Disk -Number 0",
        "shutdown.exe /s",
        "powershell -EncodedCommand AAAA",
        "Invoke-Expression $value",
        "Set-ExecutionPolicy Bypass",
        "rm -rf /",
        "Remove-Item -Recurse C:\\",
    ],
)
def test_command_policy_blocks_dangerous_commands(command: str) -> None:
    """Known catastrophic and evasive commands are rejected."""
    assert not evaluate_command(command).allowed


def test_command_policy_allows_normal_commands_and_rejects_empty() -> None:
    """Ordinary commands pass while empty input does not."""
    assert evaluate_command("Get-ChildItem").allowed
    assert not evaluate_command("  ").allowed


def test_path_policy_contains_paths_and_protects_credentials(tmp_path: Path) -> None:
    """Resolved paths remain inside the workspace and skip protected names."""
    workspace = tmp_path / "work"
    workspace.mkdir()
    policy = WorkspacePathPolicy(workspace)

    assert policy.resolve(".") == workspace.resolve()
    assert policy.resolve("") == workspace.resolve()
    assert policy.resolve("src/file.py") == (workspace / "src/file.py").resolve()
    assert policy.resolve(str(workspace / "absolute.txt")) == (workspace / "absolute.txt").resolve()
    with pytest.raises(PolicyViolation):
        policy.resolve("../outside.txt")
    with pytest.raises(PolicyViolation):
        policy.resolve(".env")
    with pytest.raises(PolicyViolation):
        policy.resolve(".", allow_root=False)
    assert policy.is_protected(workspace / ".git" / "config")
    assert policy.is_protected(tmp_path / "outside")


def test_path_policy_resolves_existing_symlink_before_containment(tmp_path: Path) -> None:
    """A symlink targeting outside the workspace cannot bypass containment."""
    workspace = tmp_path / "work"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    link = workspace / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(PolicyViolation):
        WorkspacePathPolicy(workspace).resolve("escape/secret.txt")


def test_redactor_removes_exact_and_pattern_secrets() -> None:
    """Credentials are removed before text crosses observable boundaries."""
    redactor = SecretRedactor(("exact-secret",))
    value = (
        "exact-secret Authorization: Bearer abc123 "
        "api_key=another password: hidden sk-abcdefghijklmnop"
    )
    result = redactor.redact(value)

    assert "exact-secret" not in result
    assert "abc123" not in result
    assert "another" not in result
    assert "hidden" not in result
    assert "sk-abcdefghijklmnop" not in result


def test_redactor_handles_multiline_environment_and_private_credentials() -> None:
    """Common multiline and environment credential shapes are removed."""
    value = """export OPENAI_API_KEY=sk-abcdefghijklmnop
$env:GITHUB_TOKEN='ghp_abcdefghijklmnopqrstuvwxyz'
password = multi-line-secret
-----BEGIN PRIVATE KEY-----
abc123
-----END PRIVATE KEY-----"""

    result, changed = SecretRedactor().sanitize(value)

    assert changed
    assert "abcdefghijklmnop" not in result
    assert "abcdefghijklmnopqrstuvwxyz" not in result
    assert "multi-line-secret" not in result
    assert "abc123" not in result
