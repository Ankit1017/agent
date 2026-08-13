"""Workspace containment and protected-path policy."""

from __future__ import annotations

from pathlib import Path

from local_harness.domain.errors import PolicyViolation

_PROTECTED_PARTS = frozenset(
    {
        ".env",
        ".git",
        ".harness",
        ".ssh",
        ".aws",
        ".azure",
        ".gnupg",
        ".kube",
    }
)
_PROTECTED_NAMES = frozenset({"credentials.json", "config.json", "id_rsa", "id_ed25519"})


class WorkspacePathPolicy:
    """Resolve inspection paths while enforcing a workspace boundary."""

    def __init__(self, workspace: Path) -> None:
        """Bind the policy to one canonical workspace root."""
        self.workspace = workspace.resolve(strict=True)

    def resolve(self, requested_path: str, *, allow_root: bool = True) -> Path:
        """Resolve a relative path or raise when it escapes or is protected."""
        if not requested_path.strip():
            requested_path = "."
        candidate = Path(requested_path)
        if candidate.is_absolute():
            resolved = candidate.resolve(strict=False)
        else:
            resolved = (self.workspace / candidate).resolve(strict=False)
        try:
            relative = resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise PolicyViolation("Path is outside the launch workspace") from exc
        if not allow_root and resolved == self.workspace:
            raise PolicyViolation("The workspace root is not valid for this operation")
        lowered_parts = {part.lower() for part in relative.parts}
        if lowered_parts & _PROTECTED_PARTS or resolved.name.lower() in _PROTECTED_NAMES:
            raise PolicyViolation("Path is protected and cannot be inspected")
        return resolved

    def is_protected(self, path: Path) -> bool:
        """Return whether a discovered path must be skipped."""
        try:
            relative = path.resolve(strict=False).relative_to(self.workspace)
        except ValueError:
            return True
        parts = {part.lower() for part in relative.parts}
        return bool(parts & _PROTECTED_PARTS) or path.name.lower() in _PROTECTED_NAMES
