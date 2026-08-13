"""Best-effort removal of credentials from observable text."""

from __future__ import annotations

import re

_AUTHORIZATION = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret)\b(\s*[:=]\s*)[\"']?([^\s,;\"']+)"
)
_KEY_LIKE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_ENV_ASSIGNMENT = re.compile(
    r"(?im)(\b(?:export|set)\s+|\$env:)([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD))"
    r"(\s*=\s*)[\"']?([^\s\"']+)"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    re.DOTALL,
)
_KNOWN_TOKEN = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
)


class SecretRedactor:
    """Redact configured and recognizable secrets from text."""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        """Store non-empty exact secret values for replacement."""
        self._secrets = tuple(secret for secret in secrets if secret)

    def redact(self, value: str) -> str:
        """Return text with exact and pattern-based secrets removed."""
        result = value
        for secret in self._secrets:
            result = result.replace(secret, "[REDACTED]")
        result = _AUTHORIZATION.sub(r"\1[REDACTED]", result)
        result = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", result)
        result = _ENV_ASSIGNMENT.sub(r"\1\2\3[REDACTED]", result)
        result = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", result)
        result = _KNOWN_TOKEN.sub("[REDACTED]", result)
        return _KEY_LIKE.sub("[REDACTED]", result)

    def sanitize(self, value: str) -> tuple[str, bool]:
        """Return redacted text and whether recognizable sensitive text changed."""
        redacted = self.redact(value)
        return redacted, redacted != value
