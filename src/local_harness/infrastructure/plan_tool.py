"""Model-facing adapter for persisted task-plan transitions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict

from local_harness.application.ports import SessionRepository
from local_harness.application.task_plans import TaskPlanService
from local_harness.domain.errors import ToolExecutionError
from local_harness.domain.models import Session, ToolDefinition, ToolResult


class TaskPlanTool:
    """Create and update concise observable plans for the current request."""

    def __init__(self, session: Session, sessions: SessionRepository) -> None:
        """Bind the tool to one active session and its repository."""
        self._session = session
        self._sessions = sessions

    @property
    def definition(self) -> ToolDefinition:
        """Return the closed task-plan operation schema."""
        return ToolDefinition(
            "task_plan",
            "Create, view, update, or complete the observable plan for this request.",
            {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["create", "update_step", "view", "complete"],
                    },
                    "goal": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "requires_verification": {"type": "boolean"},
                            },
                            "required": ["description"],
                            "additionalProperties": False,
                        },
                    },
                    "step_id": {"type": "integer", "minimum": 1},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "blocked"],
                    },
                    "result": {"type": "string"},
                },
                "required": ["operation"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Apply one validated operation and persist the updated session."""
        try:
            operation = arguments.get("operation")
            if not isinstance(operation, str):
                raise ToolExecutionError("operation must be a string")
            request_number = _current_request(self._session)
            service = TaskPlanService(self._session)
            if operation == "create":
                goal, steps = arguments.get("goal"), arguments.get("steps")
                if not isinstance(goal, str) or not isinstance(steps, list):
                    raise ToolExecutionError("create requires goal and steps")
                if not all(isinstance(step, dict) for step in steps):
                    raise ToolExecutionError("steps must contain objects")
                plan = service.create(request_number, goal, steps)
            elif operation == "update_step":
                step_id, status = arguments.get("step_id"), arguments.get("status")
                if not isinstance(step_id, int) or isinstance(step_id, bool):
                    raise ToolExecutionError("update_step requires integer step_id")
                if status not in {"pending", "in_progress", "completed", "blocked"}:
                    raise ToolExecutionError("update_step requires a valid status")
                result = arguments.get("result", "")
                if not isinstance(result, str):
                    raise ToolExecutionError("result must be a string")
                plan = service.update_step(request_number, step_id, status, result)
            elif operation == "view":
                existing = service.current(request_number)
                if existing is None:
                    raise ToolExecutionError("No task plan exists for this request")
                plan = existing
            elif operation == "complete":
                current = service.current(request_number)
                if (
                    current is not None
                    and any(step.requires_verification for step in current.steps)
                    and not _has_verification(self._session, request_number)
                ):
                    raise ToolExecutionError(
                        "Cannot complete plan; required verification is missing or failed"
                    )
                plan = service.complete(request_number)
            else:
                raise ToolExecutionError("Unknown task-plan operation")
            self._sessions.save(self._session)
            return ToolResult(json.dumps(asdict(plan), ensure_ascii=False, separators=(",", ":")))
        except ToolExecutionError as exc:
            return ToolResult(str(exc), True)


def _current_request(session: Session) -> int:
    values = [message.request_number for message in session.messages if message.request_number]
    if not values:
        raise ToolExecutionError("Task plans require an active user request")
    return max(values)


def _has_verification(session: Session, request_number: int) -> bool:
    """Return whether an observable verification tool succeeded for this request."""
    verification_tools = {
        "run_project_checks",
        "git_inspect",
        "code_intelligence",
        "read_files",
    }
    return any(
        event.request_number == request_number
        and event.target in verification_tools
        and event.status == "success"
        for event in session.events
    )
