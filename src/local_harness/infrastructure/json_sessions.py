"""Atomic JSON implementation of session persistence."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal, cast

from local_harness.domain.errors import SessionError
from local_harness.domain.limits import validate_max_turns
from local_harness.domain.models import (
    CompletionEvidence,
    Message,
    ProgressEvent,
    Session,
    SessionSummary,
    TaskPlan,
    TaskStep,
    ToolCall,
    WorkflowRun,
    WorkflowStageRun,
)
from local_harness.guardrails.redaction import SecretRedactor

_SESSION_ID = re.compile(r"^[a-f0-9]{32}$")
_TAG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_WORKFLOW_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class JsonSessionRepository:
    """Persist versioned session documents beneath ``.harness/sessions``."""

    def __init__(self, workspace: Path, redactor: SecretRedactor) -> None:
        """Bind storage to one workspace and redaction policy."""
        self._directory = workspace / ".harness" / "sessions"
        self._redactor = redactor

    def save(self, session: Session) -> None:
        """Redact and atomically replace the session document."""
        self._validate_id(session.session_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        session.touch()
        payload = _session_to_dict(session, self._redactor)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{session.session_id}.", suffix=".tmp", dir=self._directory
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self._path(session.session_id))
        except OSError as exc:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            finally:
                raise SessionError(f"Could not save session: {exc}") from exc

    def load(self, session_id: str) -> Session:
        """Load and validate a supported session document."""
        self._validate_id(session_id)
        try:
            payload = json.loads(self._path(session_id).read_text(encoding="utf-8"))
            return _session_from_dict(payload)
        except FileNotFoundError as exc:
            raise SessionError(f"Session not found: {session_id}") from exc
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise SessionError(f"Session is corrupt or unsupported: {session_id}") from exc

    def list_sessions(self) -> list[Session]:
        """Return valid sessions sorted by update time, skipping corrupt files."""
        if not self._directory.exists():
            return []
        sessions: list[Session] = []
        for path in self._directory.glob("*.json"):
            try:
                sessions.append(self.load(path.stem))
            except SessionError:
                continue
        return sorted(sessions, key=lambda session: session.updated_at, reverse=True)

    def delete(self, session_id: str) -> None:
        """Permanently remove one exact session document."""
        self._validate_id(session_id)
        try:
            self._path(session_id).unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise SessionError("Could not delete session") from exc

    def _path(self, session_id: str) -> Path:
        return self._directory / f"{session_id}.json"

    @staticmethod
    def _validate_id(session_id: str) -> None:
        if not _SESSION_ID.fullmatch(session_id):
            raise SessionError("Session ID must be 32 lowercase hexadecimal characters")


def _session_to_dict(session: Session, redactor: SecretRedactor) -> dict[str, object]:
    return {
        "schema_version": session.schema_version,
        "session_id": session.session_id,
        "workspace": session.workspace,
        "model": session.model,
        "max_turns_override": session.max_turns_override,
        "token_budget_override": session.token_budget_override,
        "pending_workflow_override": session.pending_workflow_override,
        "summary": _summary_to_dict(session.summary, redactor),
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "messages": [_message_to_dict(message, redactor) for message in session.messages],
        "events": [_event_to_dict(event, redactor) for event in session.events],
        "plans": [_plan_to_dict(plan, redactor) for plan in session.plans],
        "evidence": [_evidence_to_dict(item, redactor) for item in session.evidence],
        "workflows": [_workflow_to_dict(item, redactor) for item in session.workflows],
    }


def _message_to_dict(message: Message, redactor: SecretRedactor) -> dict[str, object]:
    return {
        "role": message.role,
        "content": redactor.redact(message.content) if message.content is not None else None,
        "tool_calls": [
            {
                "id": call.id,
                "name": call.name,
                "arguments": redactor.redact(call.arguments),
            }
            for call in message.tool_calls
        ],
        "tool_call_id": message.tool_call_id,
        "name": message.name,
        "request_number": message.request_number,
    }


def _event_to_dict(event: ProgressEvent, redactor: SecretRedactor) -> dict[str, object]:
    return {
        "sequence": event.sequence,
        "call_number": event.call_number,
        "kind": event.kind,
        "summary": redactor.redact(event.summary),
        "target": redactor.redact(event.target),
        "status": event.status,
        "duration_ms": event.duration_ms,
        "created_at": event.created_at,
        "request_number": event.request_number,
        "tags": list(event.tags),
        "input_tokens": event.input_tokens,
        "output_tokens": event.output_tokens,
        "usage_source": event.usage_source,
        "metadata": _redacted_metadata(event.metadata, redactor),
    }


def _summary_to_dict(
    summary: SessionSummary | None, redactor: SecretRedactor
) -> dict[str, str] | None:
    if summary is None:
        return None
    return {
        "text": redactor.redact(summary.text)[:1_000],
        "generation": summary.generation,
        "updated_at": summary.updated_at,
    }


def _plan_to_dict(plan: TaskPlan, redactor: SecretRedactor) -> dict[str, object]:
    return {
        "request_number": plan.request_number,
        "goal": redactor.redact(plan.goal)[:500],
        "status": plan.status,
        "updated_at": plan.updated_at,
        "steps": [
            {
                "step_id": step.step_id,
                "description": redactor.redact(step.description)[:300],
                "status": step.status,
                "result": redactor.redact(step.result)[:500],
                "requires_verification": step.requires_verification,
            }
            for step in plan.steps
        ],
    }


def _evidence_to_dict(evidence: CompletionEvidence, redactor: SecretRedactor) -> dict[str, object]:
    def redact(values: tuple[str, ...]) -> list[str]:
        return [redactor.redact(value)[:500] for value in values]

    return {
        "request_number": evidence.request_number,
        "changed_files": redact(evidence.changed_files),
        "checks": redact(evidence.checks),
        "sources": redact(evidence.sources),
        "limitations": redact(evidence.limitations),
        "workflow_id": evidence.workflow_id,
        "completed_stages": redact(evidence.completed_stages),
        "blocked_stages": redact(evidence.blocked_stages),
        "unmet_requirements": redact(evidence.unmet_requirements),
    }


def _workflow_to_dict(run: WorkflowRun, redactor: SecretRedactor) -> dict[str, object]:
    return {
        "request_number": run.request_number,
        "workflow_id": run.workflow_id,
        "workflow_version": run.workflow_version,
        "selection_source": run.selection_source,
        "confidence": run.confidence,
        "matched_signals": [redactor.redact(item)[:100] for item in run.matched_signals],
        "status": run.status,
        "current_stage_id": run.current_stage_id,
        "started_at": run.started_at,
        "updated_at": run.updated_at,
        "stages": [
            {
                "stage_id": stage.stage_id,
                "description": redactor.redact(stage.description)[:300],
                "status": stage.status,
                "attempts": stage.attempts,
                "result": redactor.redact(stage.result)[:500],
            }
            for stage in run.stages
        ],
    }


def _session_from_dict(payload: Any) -> Session:
    if not isinstance(payload, dict) or payload.get("schema_version") not in {1, 2, 3, 4, 5, 6, 7}:
        raise ValueError("Unsupported session schema")
    schema_version = payload["schema_version"]
    raw_messages = payload["messages"]
    if not isinstance(raw_messages, list):
        raise TypeError("messages must be a list")
    messages = []
    for item in raw_messages:
        if not isinstance(item, dict):
            raise TypeError("message must be an object")
        raw_calls = item.get("tool_calls", [])
        messages.append(
            Message(
                role=item["role"],
                content=item.get("content"),
                tool_calls=tuple(
                    ToolCall(id=call["id"], name=call["name"], arguments=call["arguments"])
                    for call in raw_calls
                ),
                tool_call_id=item.get("tool_call_id"),
                name=item.get("name"),
                request_number=_request_number(item.get("request_number"), schema_version),
            )
        )
    raw_events = payload.get("events", [])
    if not isinstance(raw_events, list):
        raise TypeError("events must be a list")
    events = [
        ProgressEvent(
            sequence=event["sequence"],
            call_number=event["call_number"],
            kind=event["kind"],
            summary=event["summary"],
            target=event["target"],
            status=event["status"],
            duration_ms=event.get("duration_ms", 0),
            created_at=event["created_at"],
            request_number=_request_number(event.get("request_number"), schema_version),
            tags=_tags(event.get("tags"), schema_version),
            input_tokens=_token_count(event.get("input_tokens"), schema_version),
            output_tokens=_token_count(event.get("output_tokens"), schema_version),
            usage_source=_usage_source(event.get("usage_source"), schema_version),
            metadata=_metadata(event.get("metadata"), schema_version),
        )
        for event in raw_events
    ]
    raw_override = payload.get("max_turns_override") if schema_version >= 3 else None
    max_turns_override = validate_max_turns(raw_override) if raw_override is not None else None
    token_budget_override = _token_budget(payload.get("token_budget_override"), schema_version)
    summary = _summary_from_dict(payload.get("summary"), schema_version)
    plans = _plans_from_dict(payload.get("plans"), schema_version)
    evidence = _evidence_from_dict(payload.get("evidence"), schema_version)
    workflows = _workflows_from_dict(payload.get("workflows"), schema_version)
    pending_workflow = _pending_workflow(payload.get("pending_workflow_override"), schema_version)
    return Session(
        schema_version=7,
        session_id=payload["session_id"],
        workspace=payload["workspace"],
        model=payload["model"],
        max_turns_override=max_turns_override,
        token_budget_override=token_budget_override,
        summary=summary,
        created_at=payload["created_at"],
        updated_at=payload["updated_at"],
        messages=messages,
        events=events,
        plans=plans,
        evidence=evidence,
        workflows=workflows,
        pending_workflow_override=pending_workflow,
    )


def _request_number(value: object, schema_version: int) -> int | None:
    if schema_version < 4 or value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("request_number must be a positive integer or null")
    return value


def _tags(value: object, schema_version: int) -> tuple[str, ...]:
    if schema_version < 5:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("tags must be a list of strings")
    if len(value) != len(set(value)) or any(not _TAG.fullmatch(item) for item in value):
        raise ValueError("tags contain invalid or duplicate labels")
    return tuple(value)


def _token_count(value: object, schema_version: int) -> int:
    if schema_version < 5:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("token counts must be non-negative integers")
    return value


def _usage_source(
    value: object, schema_version: int
) -> Literal["provider", "estimated", "unknown"]:
    if schema_version < 5:
        return "unknown"
    if value not in {"provider", "estimated", "unknown"}:
        raise ValueError("usage_source is invalid")
    return value


def _metadata(value: object, schema_version: int) -> dict[str, object]:
    if schema_version < 6 or value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("event metadata is invalid")
    return {str(key)[:100]: item for key, item in value.items()}


def _redacted_metadata(value: dict[str, object], redactor: SecretRedactor) -> dict[str, object]:
    bounded = {key[:100]: _bounded_metadata_value(item) for key, item in list(value.items())[:30]}
    serialized = json.dumps(bounded, ensure_ascii=False, default=str)
    parsed = json.loads(redactor.redact(serialized))
    return parsed if isinstance(parsed, dict) else {}


def _bounded_metadata_value(value: object) -> object:
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, list):
        return [_bounded_metadata_value(item) for item in value[:30]]
    if isinstance(value, dict):
        return {
            str(key)[:100]: _bounded_metadata_value(item) for key, item in list(value.items())[:30]
        }
    return str(value)[:500]


def _token_budget(value: object, schema_version: int) -> int | None:
    if schema_version < 5 or value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("token_budget_override must be a positive integer or null")
    return value


def _summary_from_dict(value: object, schema_version: int) -> SessionSummary | None:
    if schema_version < 5 or value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("summary must be an object or null")
    text = value.get("text")
    generation = value.get("generation")
    updated_at = value.get("updated_at")
    if not isinstance(text, str) or generation not in {"deterministic", "llm"}:
        raise ValueError("summary is invalid")
    if not isinstance(updated_at, str):
        raise ValueError("summary updated_at is invalid")
    return SessionSummary(
        text[:1_000], cast(Literal["deterministic", "llm"], generation), updated_at
    )


def _plans_from_dict(value: object, schema_version: int) -> list[TaskPlan]:
    if schema_version < 6:
        return []
    if not isinstance(value, list):
        raise ValueError("plans must be a list")
    plans: list[TaskPlan] = []
    for raw in value:
        if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list):
            raise ValueError("plan is invalid")
        request_number = _request_number(raw.get("request_number"), schema_version)
        goal, status, updated_at = raw.get("goal"), raw.get("status"), raw.get("updated_at")
        if request_number is None or not isinstance(goal, str) or not isinstance(updated_at, str):
            raise ValueError("plan metadata is invalid")
        if status not in {"active", "completed", "blocked"}:
            raise ValueError("plan status is invalid")
        steps: list[TaskStep] = []
        for step in raw["steps"]:
            if not isinstance(step, dict):
                raise ValueError("plan step is invalid")
            step_id, description = step.get("step_id"), step.get("description")
            step_status, result = step.get("status"), step.get("result", "")
            verification = step.get("requires_verification", False)
            if not isinstance(step_id, int) or isinstance(step_id, bool) or step_id <= 0:
                raise ValueError("plan step id is invalid")
            if not isinstance(description, str) or not isinstance(result, str):
                raise ValueError("plan step text is invalid")
            if step_status not in {"pending", "in_progress", "completed", "blocked"}:
                raise ValueError("plan step status is invalid")
            if not isinstance(verification, bool):
                raise ValueError("plan verification flag is invalid")
            steps.append(
                TaskStep(step_id, description[:300], step_status, result[:500], verification)
            )
        if len({step.step_id for step in steps}) != len(steps):
            raise ValueError("plan step ids must be unique")
        plans.append(TaskPlan(request_number, goal[:500], tuple(steps), status, updated_at))
    return plans


def _evidence_from_dict(value: object, schema_version: int) -> list[CompletionEvidence]:
    if schema_version < 6:
        return []
    if not isinstance(value, list):
        raise ValueError("evidence must be a list")
    items: list[CompletionEvidence] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("evidence is invalid")
        request_number = _request_number(raw.get("request_number"), schema_version)
        if request_number is None:
            raise ValueError("evidence request number is invalid")
        groups: list[tuple[str, ...]] = []
        names = ["changed_files", "checks", "sources", "limitations"]
        if schema_version >= 7:
            names.extend(["completed_stages", "blocked_stages", "unmet_requirements"])
        for name in names:
            group = raw.get(name, [])
            if not isinstance(group, list) or not all(isinstance(item, str) for item in group):
                raise ValueError(f"evidence {name} is invalid")
            groups.append(tuple(item[:500] for item in group))
        if schema_version < 7:
            items.append(
                CompletionEvidence(
                    request_number,
                    groups[0],
                    groups[1],
                    groups[2],
                    groups[3],
                )
            )
        else:
            workflow_id = raw.get("workflow_id", "")
            if not isinstance(workflow_id, str):
                raise ValueError("evidence workflow_id is invalid")
            items.append(
                CompletionEvidence(
                    request_number,
                    groups[0],
                    groups[1],
                    groups[2],
                    groups[3],
                    workflow_id=workflow_id[:64],
                    completed_stages=groups[4],
                    blocked_stages=groups[5],
                    unmet_requirements=groups[6],
                )
            )
    return items


def _pending_workflow(value: object, schema_version: int) -> str | None:
    if schema_version < 7 or value is None:
        return None
    if not isinstance(value, str) or not _WORKFLOW_ID.fullmatch(value):
        raise ValueError("pending_workflow_override is invalid")
    return value


def _workflows_from_dict(value: object, schema_version: int) -> list[WorkflowRun]:
    if schema_version < 7:
        return []
    if not isinstance(value, list):
        raise ValueError("workflows must be a list")
    runs: list[WorkflowRun] = []
    for raw in value:
        if not isinstance(raw, dict) or not isinstance(raw.get("stages"), list):
            raise ValueError("workflow run is invalid")
        request_number = _request_number(raw.get("request_number"), schema_version)
        workflow_id = raw.get("workflow_id")
        version = raw.get("workflow_version")
        source = raw.get("selection_source")
        confidence = raw.get("confidence")
        status = raw.get("status")
        current = raw.get("current_stage_id", "")
        started_at, updated_at = raw.get("started_at"), raw.get("updated_at")
        signals = raw.get("matched_signals", [])
        if (
            request_number is None
            or not isinstance(workflow_id, str)
            or not _WORKFLOW_ID.fullmatch(workflow_id)
            or not isinstance(version, int)
            or isinstance(version, bool)
            or version <= 0
            or source not in {"automatic", "explicit", "pending", "fallback"}
            or not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0 <= confidence <= 1
            or status not in {"active", "completed", "blocked"}
            or not isinstance(current, str)
            or not isinstance(started_at, str)
            or not isinstance(updated_at, str)
            or not isinstance(signals, list)
            or not all(isinstance(item, str) for item in signals)
        ):
            raise ValueError("workflow run metadata is invalid")
        stages: list[WorkflowStageRun] = []
        for stage in raw["stages"]:
            if not isinstance(stage, dict):
                raise ValueError("workflow stage is invalid")
            stage_id, description = stage.get("stage_id"), stage.get("description")
            stage_status = stage.get("status")
            attempts, result = stage.get("attempts"), stage.get("result", "")
            if (
                not isinstance(stage_id, str)
                or not _WORKFLOW_ID.fullmatch(stage_id)
                or not isinstance(description, str)
                or stage_status not in {"pending", "in_progress", "completed", "skipped", "blocked"}
                or not isinstance(attempts, int)
                or isinstance(attempts, bool)
                or not 0 <= attempts <= 5
                or not isinstance(result, str)
            ):
                raise ValueError("workflow stage metadata is invalid")
            stages.append(
                WorkflowStageRun(
                    stage_id,
                    description[:300],
                    stage_status,
                    attempts,
                    result[:500],
                )
            )
        runs.append(
            WorkflowRun(
                request_number,
                workflow_id,
                version,
                source,
                float(confidence),
                tuple(item[:100] for item in signals[:10]),
                tuple(stages),
                status,
                current[:64],
                started_at,
                updated_at,
            )
        )
    return runs
