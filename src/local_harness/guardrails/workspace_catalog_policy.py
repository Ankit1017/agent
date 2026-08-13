"""Safety policy for browser-managed workspace roots."""

from __future__ import annotations

import os
from pathlib import Path

from local_harness.domain.errors import PolicyViolation

_REPARSE_POINT = 0x400


class WorkspaceCatalogPolicy:
    """Resolve and validate a user-supplied local Windows project directory."""

    def resolve(self, raw_path: str) -> Path:
        """Return a canonical safe directory or raise a policy violation."""
        value = raw_path.strip()
        if not value:
            raise PolicyViolation("Workspace path cannot be empty")
        candidate = Path(value)
        if not candidate.is_absolute():
            raise PolicyViolation("Workspace path must be absolute")
        if value.startswith(("\\\\", "//")):
            raise PolicyViolation("Network and UNC workspaces are not supported")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise PolicyViolation("Workspace path does not exist or is unreadable") from exc
        if not resolved.is_dir():
            raise PolicyViolation("Workspace path must be a directory")
        if resolved.parent == resolved:
            raise PolicyViolation("Drive and filesystem roots cannot be workspaces")
        attributes = getattr(resolved.stat(), "st_file_attributes", 0)
        if attributes & _REPARSE_POINT:
            raise PolicyViolation("Symlink and junction workspace roots are not supported")
        protected = _protected_roots()
        if any(resolved == root or root in resolved.parents for root in protected):
            raise PolicyViolation("Protected system directories cannot be workspaces")
        return resolved


def _protected_roots() -> tuple[Path, ...]:
    """Return existing Windows system roots without exposing environment values."""
    values = (
        os.environ.get("WINDIR", "C:\\Windows"),
        os.environ.get("ProgramFiles", "C:\\Program Files"),
        os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
        os.environ.get("ProgramData", "C:\\ProgramData"),
    )
    return tuple(Path(value).resolve() for value in values if value and Path(value).exists())
