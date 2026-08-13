"""Thread-safe presentation bridge for browser progress and approvals."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from threading import Event, Lock

from local_harness.domain.models import ApprovalDecision, ProgressEvent
from local_harness.domain.web_ui import WebEvent
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.identifiers import new_session_id
from local_harness.interfaces.web.events import WebEventHub


@dataclass(slots=True)
class _PendingApproval:
    approval_id: str
    owner: str
    completed: Event
    decision: ApprovalDecision


class WebPresentationBridge:
    """Forward synchronous runtime interactions to the asynchronous browser channel."""

    def __init__(
        self,
        redactor: SecretRedactor,
        hub: WebEventHub,
        workspace_id: str,
        *,
        approval_timeout_seconds: int = 600,
    ) -> None:
        """Create an inactive workspace bridge."""
        self._redactor = redactor
        self._hub = hub
        self._workspace_id = workspace_id
        self._timeout = approval_timeout_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task_id = ""
        self._session_id = ""
        self._owner = ""
        self._pending: dict[str, _PendingApproval] = {}
        self._lock = Lock()

    def activate(
        self,
        loop: asyncio.AbstractEventLoop,
        task_id: str,
        session_id: str,
        owner: str,
    ) -> None:
        """Bind subsequent progress and approvals to one active task."""
        with self._lock:
            self._loop = loop
            self._task_id = task_id
            self._session_id = session_id
            self._owner = owner

    def deactivate(self) -> None:
        """Clear task routing after rejecting unresolved approvals."""
        self.reject_all("task ended")
        with self._lock:
            self._task_id = self._session_id = self._owner = ""

    def publish(self, event: ProgressEvent) -> None:
        """Publish one redacted persisted progress event."""
        payload = asdict(event)
        payload["summary"] = self._redactor.redact(event.summary)
        payload["target"] = self._redactor.redact(event.target)
        self._emit("progress", payload, request_number=event.request_number)

    def request(self, command: str, explanation: str, workspace: str) -> ApprovalDecision:
        """Request approval for one exact PowerShell command."""
        return self._request(
            "command",
            {
                "command": self._redactor.redact(command),
                "explanation": self._redactor.redact(explanation),
                "workspace": workspace,
                "warning": "Approved PowerShell runs natively without an OS sandbox.",
            },
        )

    def request_patch(self, preview: str, explanation: str, workspace: str) -> ApprovalDecision:
        """Request approval for one exact redacted patch."""
        return self._request(
            "patch",
            {
                "preview": self._redactor.redact(preview),
                "explanation": self._redactor.redact(explanation),
                "workspace": workspace,
            },
        )

    def request_maintenance(self, action: str, details: str) -> ApprovalDecision:
        """Request approval for recoverable session maintenance."""
        return self._request(
            "maintenance",
            {"action": self._redactor.redact(action), "details": self._redactor.redact(details)},
        )

    def resolve(self, approval_id: str, client_id: str, approved: bool, feedback: str = "") -> bool:
        """Resolve an approval only for its originating browser client."""
        with self._lock:
            pending = self._pending.get(approval_id)
            if pending is None or pending.owner != client_id:
                return False
            pending.decision = ApprovalDecision(approved, self._redactor.redact(feedback)[:1_000])
            pending.completed.set()
            return True

    def reject_owner(self, client_id: str, reason: str = "browser disconnected") -> None:
        """Reject every pending approval owned by one disconnected browser."""
        with self._lock:
            values = [item for item in self._pending.values() if item.owner == client_id]
            for pending in values:
                pending.decision = ApprovalDecision(False, reason)
                pending.completed.set()

    def reject_all(self, reason: str = "server shutdown") -> None:
        """Reject every unresolved approval."""
        with self._lock:
            for pending in self._pending.values():
                pending.decision = ApprovalDecision(False, reason)
                pending.completed.set()

    def has_pending(self) -> bool:
        """Return whether this workspace has an unresolved approval."""
        with self._lock:
            return bool(self._pending)

    def _request(self, kind: str, payload: dict[str, object]) -> ApprovalDecision:
        with self._lock:
            if not self._owner or self._loop is None:
                return ApprovalDecision(False, "No connected task owner")
            approval_id = new_session_id()
            pending = _PendingApproval(approval_id, self._owner, Event(), ApprovalDecision(False))
            self._pending[approval_id] = pending
        self._emit("approval.requested", {"approval_id": approval_id, "kind": kind, **payload})
        completed = pending.completed.wait(self._timeout)
        with self._lock:
            self._pending.pop(approval_id, None)
        if not completed:
            pending.decision = ApprovalDecision(False, "Approval expired")
            self._emit("approval.expired", {"approval_id": approval_id, "kind": kind})
        else:
            self._emit(
                "approval.resolved",
                {"approval_id": approval_id, "approved": pending.decision.approved},
            )
        return pending.decision

    def _emit(
        self, event_type: str, payload: dict[str, object], request_number: int | None = None
    ) -> None:
        with self._lock:
            loop = self._loop
            event = WebEvent(
                0,
                event_type,
                self._workspace_id,
                self._session_id,
                self._task_id,
                request_number,
                payload,
            )
        if loop is not None and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._hub.publish(event), loop)
