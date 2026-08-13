"""Session analytics, tagging, filtering, and maintenance use cases."""

from __future__ import annotations

import re
from dataclasses import replace

from local_harness.application.ports import (
    SessionArchiver,
    SessionExporter,
    SessionIntegrityChecker,
    SessionMaintenanceGateway,
    SessionRepository,
)
from local_harness.domain.errors import SessionError
from local_harness.domain.maintenance import ArchiveInfo, ExportResult, IntegrityFinding
from local_harness.domain.models import ProgressEvent, Session

_TAG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class SessionService:
    """Coordinate session metadata and filesystem maintenance through ports."""

    def __init__(
        self,
        repository: SessionRepository,
        exporter: SessionExporter,
        archiver: SessionArchiver,
        integrity: SessionIntegrityChecker,
        approval: SessionMaintenanceGateway,
    ) -> None:
        """Store session persistence and maintenance boundaries."""
        self._repository = repository
        self._exporter = exporter
        self._archiver = archiver
        self._integrity = integrity
        self._approval = approval

    def add_tag(self, session: Session, sequence: int, label: str) -> str:
        """Add one normalized tag to an existing event."""
        normalized = _normalize_tag(label)
        index = _event_index(session, sequence)
        event = session.events[index]
        if normalized in event.tags:
            raise SessionError(f"Event {sequence} already has tag {normalized}")
        session.events[index] = replace(event, tags=(*event.tags, normalized))
        self._repository.save(session)
        return normalized

    def remove_tag(self, session: Session, sequence: int, label: str) -> str:
        """Remove one existing normalized event tag."""
        normalized = _normalize_tag(label)
        index = _event_index(session, sequence)
        event = session.events[index]
        if normalized not in event.tags:
            raise SessionError(f"Event {sequence} does not have tag {normalized}")
        session.events[index] = replace(
            event, tags=tuple(tag for tag in event.tags if tag != normalized)
        )
        self._repository.save(session)
        return normalized

    def tagged_events(self, session: Session, label: str = "") -> list[ProgressEvent]:
        """Return tagged events, optionally restricted to one normalized label."""
        normalized = _normalize_tag(label) if label else ""
        return [
            event
            for event in session.events
            if event.tags and (not normalized or normalized in event.tags)
        ]

    def filter_events(self, session: Session, query: str) -> list[ProgressEvent]:
        """Filter events by model/tool/state/tag selector or free text."""
        value = query.strip().casefold()
        if not value:
            return list(session.events)
        if value == "model":
            return [event for event in session.events if event.kind.startswith("model_")]
        if value == "tool":
            return [event for event in session.events if event.kind.startswith("tool_")]
        if value in {"error", "errors"}:
            return [event for event in session.events if event.status == "error"]
        if value in {"running", "started"}:
            return [event for event in session.events if event.status == "started"]
        if value == "tagged":
            return [event for event in session.events if event.tags]
        if value.startswith("tag:"):
            return self.tagged_events(session, value[4:])
        return [
            event
            for event in session.events
            if value
            in " ".join(
                (event.summary, event.target, event.kind, event.status, *event.tags)
            ).casefold()
        ]

    def export(self, session: Session, format_name: str) -> ExportResult:
        """Export one loaded session in Markdown or CSV format."""
        return self._exporter.export(session, format_name)

    def archive(self, session_id: str) -> ArchiveInfo:
        """Archive a session after explicit maintenance approval."""
        decision = self._approval.request_maintenance("Archive session", session_id)
        if not decision.approved:
            raise SessionError("Session archive rejected")
        return self._archiver.archive(session_id)

    def list_archives(self) -> list[ArchiveInfo]:
        """List available session archives."""
        return self._archiver.list_archives()

    def restore(self, session_id: str) -> Session:
        """Restore a validated archived session."""
        return self._archiver.restore(session_id)

    def scan(self) -> list[IntegrityFinding]:
        """Return current session-integrity findings."""
        return self._integrity.scan()

    def quarantine(self, check_id: str) -> str:
        """Quarantine an unchanged finding after explicit approval."""
        finding = next((item for item in self._integrity.scan() if item.check_id == check_id), None)
        if finding is None:
            raise SessionError("Integrity finding is missing or stale")
        decision = self._approval.request_maintenance(
            "Quarantine corrupt session", f"{finding.filename}: {finding.reason}"
        )
        if not decision.approved:
            raise SessionError("Session quarantine rejected")
        return self._integrity.quarantine(check_id)


def session_info(session: Session, token_budget: int) -> str:
    """Render a concise provider-neutral session information block."""
    usage = sum(event.input_tokens + event.output_tokens for event in session.events)
    tags = sorted({tag for event in session.events for tag in event.tags})
    requests = len(
        {message.request_number for message in session.messages if message.request_number}
    )
    summary = session.summary.text if session.summary else "No summary yet"
    budget = str(token_budget) if token_budget else "disabled"
    calls = max((event.call_number for event in session.events), default=0)
    workflow = session.workflows[-1] if session.workflows else None
    workflow_text = (
        f"{workflow.workflow_id} [{workflow.status}]" if workflow is not None else "none"
    )
    return (
        f"Session: {session.session_id}\nModel: {session.model}\n"
        f"Created: {session.created_at}\nUpdated: {session.updated_at}\n"
        f"Requests: {requests}\nLLM calls: {calls}\n"
        f"Tokens: {usage} / {budget}\nWorkflow: {workflow_text}\n"
        f"Tags: {', '.join(tags) if tags else '(none)'}\n"
        f"Summary ({session.summary.generation if session.summary else 'none'}): {summary}"
    )


def _normalize_tag(label: str) -> str:
    normalized = label.strip().casefold()
    if not _TAG.fullmatch(normalized):
        raise SessionError("Tag must be 1-32 lowercase letters, numbers, dashes, or underscores")
    return normalized


def _event_index(session: Session, sequence: int) -> int:
    for index, event in enumerate(session.events):
        if event.sequence == sequence:
            return index
    raise SessionError(f"Progress event not found: {sequence}")
