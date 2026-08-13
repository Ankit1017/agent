"""Conservative deny policy for free-form PowerShell commands."""

from __future__ import annotations

import re

from local_harness.domain.models import PolicyDecision

_DENIED_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(format-volume|clear-disk|initialize-disk|diskpart)\b", re.I),
        "disk modification is forbidden",
    ),
    (
        re.compile(r"\b(stop-computer|restart-computer|shutdown(?:\.exe)?)\b", re.I),
        "system shutdown or restart is forbidden",
    ),
    (re.compile(r"(?:-|/)encodedcommand\b", re.I), "encoded PowerShell is forbidden"),
    (re.compile(r"\b(?:iex|invoke-expression)\b", re.I), "dynamic command evaluation is forbidden"),
    (
        re.compile(r"set-executionpolicy\s+(?:bypass|unrestricted)", re.I),
        "execution-policy bypass is forbidden",
    ),
    (
        re.compile(r"\brm\s+-[a-z]*r[a-z]*f[a-z]*\s+[/~](?:\s|$)", re.I),
        "recursive root deletion is forbidden",
    ),
    (
        re.compile(
            r"\bremove-item\b(?=[^\r\n]*(?:-recurse|-r\b))(?=[^\r\n]*(?:[a-z]:\\(?:\s|$)|\\(?:\s|$)|\$env:systemdrive))",
            re.I,
        ),
        "recursive deletion of a filesystem root is forbidden",
    ),
)


def evaluate_command(command: str) -> PolicyDecision:
    """Return a deterministic decision for one proposed PowerShell command."""
    normalized = command.strip()
    if not normalized:
        return PolicyDecision(False, "empty commands are not allowed")
    for pattern, reason in _DENIED_PATTERNS:
        if pattern.search(normalized):
            return PolicyDecision(False, reason)
    return PolicyDecision(True)
