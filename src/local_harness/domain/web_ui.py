"""Provider-neutral records for the local browser interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    """One allowlisted browser workspace."""

    workspace_id: str
    label: str
    path: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    is_control: bool = False


@dataclass(frozen=True, slots=True)
class WebTask:
    """Observable state for one browser-submitted agent request."""

    task_id: str
    workspace_id: str
    session_id: str
    client_id: str
    state: Literal["queued", "running", "completed", "failed", "cancelling", "cancelled"]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    response: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class WebEvent:
    """Versioned event delivered to browser clients."""

    event_id: int
    type: str
    workspace_id: str = ""
    session_id: str = ""
    task_id: str = ""
    request_number: int | None = None
    payload: dict[str, object] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    version: int = 1
