"""Thread-safe bridge between synchronous application ports and Textual."""

from __future__ import annotations

from threading import Event, Lock
from typing import TYPE_CHECKING

from local_harness.domain.models import ApprovalDecision, ProgressEvent
from local_harness.guardrails.redaction import SecretRedactor

if TYPE_CHECKING:
    from local_harness.interfaces.tui.app import HarnessApp


class TuiBridge:
    """Forward progress and approval interactions to a bound Textual app."""

    def __init__(self, redactor: SecretRedactor) -> None:
        """Create an unbound bridge using the central redaction policy."""
        self._redactor = redactor
        self._app: HarnessApp | None = None
        self._pending: set[Event] = set()
        self._lock = Lock()

    def bind(self, app: HarnessApp) -> None:
        """Bind the bridge immediately before the application starts."""
        self._app = app

    def publish(self, event: ProgressEvent) -> None:
        """Safely deliver a redacted progress event from the agent worker."""
        app = self._require_app()
        clean = ProgressEvent(
            sequence=event.sequence,
            call_number=event.call_number,
            kind=event.kind,
            summary=self._redactor.redact(event.summary),
            target=self._redactor.redact(event.target),
            status=event.status,
            duration_ms=event.duration_ms,
            created_at=event.created_at,
            request_number=event.request_number,
            tags=event.tags,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
            usage_source=event.usage_source,
        )
        app.call_from_thread(app.show_progress, clean)

    def request(self, command: str, explanation: str, workspace: str) -> ApprovalDecision:
        """Request approval in a modal while blocking only the agent worker."""
        app = self._require_app()
        completed = Event()
        result = [ApprovalDecision(False)]

        def receive(decision: ApprovalDecision | None) -> None:
            result[0] = decision or ApprovalDecision(False)
            completed.set()

        with self._lock:
            self._pending.add(completed)
        app.call_from_thread(
            app.request_approval,
            self._redactor.redact(command),
            self._redactor.redact(explanation),
            workspace,
            receive,
        )
        completed.wait()
        with self._lock:
            self._pending.discard(completed)
        return result[0]

    def request_patch(self, preview: str, explanation: str, workspace: str) -> ApprovalDecision:
        """Request exact patch approval while blocking only the agent worker."""
        app = self._require_app()
        completed = Event()
        result = [ApprovalDecision(False)]

        def receive(decision: ApprovalDecision | None) -> None:
            result[0] = decision or ApprovalDecision(False)
            completed.set()

        with self._lock:
            self._pending.add(completed)
        app.call_from_thread(
            app.request_patch_approval,
            self._redactor.redact(preview),
            self._redactor.redact(explanation),
            workspace,
            receive,
        )
        completed.wait()
        with self._lock:
            self._pending.discard(completed)
        return result[0]

    def request_maintenance(self, action: str, details: str) -> ApprovalDecision:
        """Request default-reject session maintenance confirmation."""
        app = self._require_app()
        completed = Event()
        result = [ApprovalDecision(False)]

        def receive(decision: ApprovalDecision | None) -> None:
            result[0] = decision or ApprovalDecision(False)
            completed.set()

        with self._lock:
            self._pending.add(completed)
        app.call_from_thread(
            app.request_maintenance_approval,
            self._redactor.redact(action),
            self._redactor.redact(details),
            receive,
        )
        completed.wait()
        with self._lock:
            self._pending.discard(completed)
        return result[0]

    def reject_pending(self) -> None:
        """Reject and unblock every approval if the application is closing."""
        with self._lock:
            pending = tuple(self._pending)
        for completed in pending:
            completed.set()

    def _require_app(self) -> HarnessApp:
        if self._app is None:
            raise RuntimeError("TUI bridge is not bound to an application")
        return self._app
