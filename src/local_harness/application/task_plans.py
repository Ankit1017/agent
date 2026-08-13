"""Pure state transitions for persisted observable task plans."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

from local_harness.domain.errors import ToolExecutionError
from local_harness.domain.models import Session, TaskPlan, TaskStep


class TaskPlanService:
    """Validate and apply task-plan state transitions to one session."""

    def __init__(self, session: Session) -> None:
        """Bind plan operations to a mutable session aggregate."""
        self._session = session

    def current(self, request_number: int) -> TaskPlan | None:
        """Return the plan for a request when one exists."""
        return next(
            (plan for plan in self._session.plans if plan.request_number == request_number), None
        )

    def create(self, request_number: int, goal: str, steps: list[dict[str, object]]) -> TaskPlan:
        """Create one bounded plan, replacing no existing request plan."""
        if self.current(request_number) is not None:
            raise ToolExecutionError("A task plan already exists for this request")
        clean_goal = _text(goal, "goal", 500)
        if not 1 <= len(steps) <= 12:
            raise ToolExecutionError("steps must contain between 1 and 12 entries")
        values: list[TaskStep] = []
        for index, raw in enumerate(steps, start=1):
            description = _text(raw.get("description"), "step description", 300)
            verification = raw.get("requires_verification", False)
            if not isinstance(verification, bool):
                raise ToolExecutionError("requires_verification must be a Boolean")
            values.append(TaskStep(index, description, requires_verification=verification))
        plan = TaskPlan(request_number, clean_goal, tuple(values))
        self._session.plans.append(plan)
        return plan

    def update_step(
        self,
        request_number: int,
        step_id: int,
        status: Literal["pending", "in_progress", "completed", "blocked"],
        result: str,
    ) -> TaskPlan:
        """Update one step while enforcing a single in-progress step."""
        plan = self._required(request_number)
        if status not in {"pending", "in_progress", "completed", "blocked"}:
            raise ToolExecutionError("status is invalid")
        if status == "in_progress" and any(
            step.step_id != step_id and step.status == "in_progress" for step in plan.steps
        ):
            raise ToolExecutionError("Only one task-plan step may be in progress")
        found = False
        updated: list[TaskStep] = []
        for step in plan.steps:
            if step.step_id != step_id:
                updated.append(step)
                continue
            found = True
            clean_result = _optional_text(result, "result", 500)
            if status == "completed" and step.requires_verification and not clean_result:
                raise ToolExecutionError("A verification step requires a result before completion")
            updated.append(replace(step, status=status, result=clean_result))
        if not found:
            raise ToolExecutionError(f"Unknown task-plan step: {step_id}")
        state: Literal["active", "completed", "blocked"] = (
            "blocked" if any(step.status == "blocked" for step in updated) else "active"
        )
        return self._replace(plan, replace(plan, steps=tuple(updated), status=state))

    def complete(self, request_number: int) -> TaskPlan:
        """Mark a plan complete only after every step has evidence of completion."""
        plan = self._required(request_number)
        incomplete = [step.step_id for step in plan.steps if step.status != "completed"]
        if incomplete:
            raise ToolExecutionError(f"Cannot complete plan; unfinished steps: {incomplete}")
        if any(step.requires_verification and not step.result for step in plan.steps):
            raise ToolExecutionError("Cannot complete plan without verification evidence")
        return self._replace(plan, replace(plan, status="completed"))

    def _required(self, request_number: int) -> TaskPlan:
        plan = self.current(request_number)
        if plan is None:
            raise ToolExecutionError("No task plan exists for this request")
        return plan

    def _replace(self, old: TaskPlan, new: TaskPlan) -> TaskPlan:
        value = replace(new, updated_at=datetime.now(UTC).isoformat())
        index = self._session.plans.index(old)
        self._session.plans[index] = value
        return value


def _text(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolExecutionError(f"{name} must be a non-empty string")
    return " ".join(value.split())[:limit]


def _optional_text(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ToolExecutionError(f"{name} must be a string")
    return " ".join(value.split())[:limit]
