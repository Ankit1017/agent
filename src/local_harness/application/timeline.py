"""Pure request-level activity timeline projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from local_harness.domain.models import ProgressEvent


@dataclass(frozen=True, slots=True)
class TimelineStep:
    """One concise observable step projected from persisted progress events."""

    key: str
    label: str
    target: str
    status: Literal["started", "success", "warning", "error"]
    duration_ms: int
    sequence: int


def build_request_timeline(
    events: list[ProgressEvent], request_number: int | None
) -> list[TimelineStep]:
    """Merge each model tool request with its matching tool result."""
    relevant = [event for event in events if event.request_number == request_number]
    steps: list[TimelineStep] = []
    consumed_tools: set[int] = set()
    for event in relevant:
        if event.kind == "model_start":
            continue
        if event.kind == "model_complete" and event.target != "final":
            tools = [
                candidate
                for candidate in relevant
                if candidate.call_number == event.call_number
                and candidate.kind in {"tool_complete", "tool_error", "plan_update"}
                and candidate.sequence not in consumed_tools
            ]
            if tools:
                for tool in tools:
                    consumed_tools.add(tool.sequence)
                    steps.append(
                        TimelineStep(
                            f"tool-{tool.sequence}",
                            event.summary,
                            tool.target,
                            tool.status,
                            event.duration_ms + tool.duration_ms,
                            tool.sequence,
                        )
                    )
                continue
        if event.sequence in consumed_tools or event.kind in {
            "tool_complete",
            "tool_error",
            "plan_update",
        }:
            if event.sequence in consumed_tools:
                continue
        steps.append(
            TimelineStep(
                f"event-{event.sequence}",
                event.summary,
                event.target,
                event.status,
                event.duration_ms,
                event.sequence,
            )
        )
    return steps
