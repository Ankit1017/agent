"""Domain records for session exports, archives, integrity, and plugins."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExportResult:
    """One successfully written session export."""

    path: str
    format: str


@dataclass(frozen=True, slots=True)
class ArchiveInfo:
    """Metadata for one recoverable session archive."""

    session_id: str
    archived_at: str
    summary: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class IntegrityFinding:
    """A stable reference to one suspicious session artifact."""

    check_id: str
    filename: str
    reason: str
    size_bytes: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class PluginStatus:
    """Discovery and loading state for one tool plugin."""

    name: str
    state: str
    tools: tuple[str, ...] = ()
    detail: str = ""
