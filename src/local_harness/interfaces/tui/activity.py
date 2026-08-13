"""Inline, request-scoped observable activity timeline."""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Collapsible, Static

from local_harness.application.timeline import TimelineStep, build_request_timeline
from local_harness.domain.models import ProgressEvent


class RequestActivity(Collapsible):
    """Show one request's observable work in a collapsed-by-default timeline."""

    def __init__(
        self,
        request_number: int,
        events: list[ProgressEvent] | None = None,
    ) -> None:
        """Create a request timeline and preload persisted events."""
        self.request_number = request_number
        self._events: list[ProgressEvent] = []
        self._steps: list[TimelineStep] = []
        self._rows: dict[str, Static] = {}
        self._finished = False
        self._timeline = Vertical(classes="request-activity-timeline")
        super().__init__(
            self._timeline,
            title="Working…",
            collapsed=True,
            classes="request-activity running",
        )
        for event in events or []:
            self.record(event)

    @property
    def step_count(self) -> int:
        """Return the visible step count after lifecycle events are merged."""
        return len(self._steps)

    @property
    def total_duration_ms(self) -> int:
        """Return the sum of projected step durations."""
        return sum(step.duration_ms for step in self._steps)

    def on_mount(self) -> None:
        """Render records accumulated before the widget entered the DOM."""
        self._render_steps()

    def record(self, event: ProgressEvent) -> None:
        """Add or update one observable event without changing collapse state."""
        if event.request_number not in {None, self.request_number}:
            return
        index = next(
            (
                position
                for position, item in enumerate(self._events)
                if item.sequence == event.sequence
            ),
            -1,
        )
        if index < 0:
            self._events.append(event)
        else:
            self._events[index] = event
        self._steps = build_request_timeline(self._events, self.request_number)
        if self.is_mounted:
            self._timeline.remove_children()
            self._render_steps()
        if not self._finished:
            self.title = f"Working… · {event.summary}"

    def finish(self, *, failed: bool) -> None:
        """Set the terminal request status while preserving manual expansion."""
        self._finished = True
        has_step_errors = any(step.status == "error" for step in self._steps)
        self.set_class(not failed and not has_step_errors, "completed")
        self.set_class(not failed and has_step_errors, "warning-state")
        self.set_class(failed, "failed")
        self.remove_class("running")
        steps = self.step_count
        if failed:
            self.title = f"Stopped with error · {steps} {_step_word(steps)}"
        elif has_step_errors:
            self.title = (
                f"Completed with issues · {steps} {_step_word(steps)} · "
                f"{self.total_duration_ms / 1000:.1f}s"
            )
        else:
            self.title = (
                f"Completed · {steps} {_step_word(steps)} · {self.total_duration_ms / 1000:.1f}s"
            )

    def _render_steps(self) -> None:
        """Mount the current pure timeline projection."""
        self._rows.clear()
        for step in self._steps:
            row = Static(_format_timeline_step(step), markup=False)
            self._rows[step.key] = row
            self._timeline.mount(row)


def _format_timeline_step(step: TimelineStep) -> str:
    state = {
        "started": "RUNNING",
        "success": "OK",
        "warning": "WARN",
        "error": "ERROR",
    }[step.status]
    return f"[{state}] {step.label} -> {step.target} - {step.duration_ms / 1000:.1f}s"


def _step_word(count: int) -> str:
    return "step" if count == 1 else "steps"
