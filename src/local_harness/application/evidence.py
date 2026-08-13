"""Deterministic completion-evidence extraction and rendering."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from local_harness.domain.models import (
    CompletionEvidence,
    Message,
    ProgressEvent,
    TaskPlan,
    WorkflowRun,
)


def build_completion_evidence(
    messages: Sequence[Message],
    events: Sequence[ProgressEvent],
    plans: Sequence[TaskPlan],
    request_number: int,
    workflows: Sequence[WorkflowRun] = (),
) -> CompletionEvidence:
    """Build bounded evidence from successful observable tool records."""
    changed: list[str] = []
    checks: list[str] = []
    sources: list[str] = []
    for message in messages:
        if message.role != "tool" or message.request_number != request_number:
            continue
        payload = _payload(message.content)
        if payload is None:
            continue
        items = payload.get("items", [])
        if not isinstance(items, list):
            continue
        if message.name == "apply_patch":
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    _append_unique(changed, item["path"])
        elif message.name == "run_project_checks":
            for item in items:
                if not isinstance(item, dict):
                    continue
                profile = str(item.get("profile", "check"))
                status = str(item.get("status", item.get("outcome", "unknown")))
                _append_unique(checks, f"{profile}: {status}"[:240])
        elif message.name in {"web_search", "read_web_pages"}:
            key = "final_url" if message.name == "read_web_pages" else "url"
            for item in items:
                if isinstance(item, dict) and isinstance(item.get(key), str):
                    _append_unique(sources, item[key])
    limitations = [
        f"{event.target}: {event.summary}"[:240]
        for event in events
        if event.request_number == request_number and event.status == "error"
    ]
    for event in events:
        if event.request_number != request_number or event.kind != "memory_retrieval":
            continue
        paths = event.metadata.get("selected_paths", [])
        if isinstance(paths, list):
            for path in paths:
                if isinstance(path, str):
                    _append_unique(sources, path)
    plan = next((item for item in plans if item.request_number == request_number), None)
    if plan is not None and plan.status != "completed":
        limitations.append(f"Task plan remains {plan.status}")
    workflow = next((item for item in workflows if item.request_number == request_number), None)
    completed_stages = (
        tuple(stage.stage_id for stage in workflow.stages if stage.status == "completed")
        if workflow is not None
        else ()
    )
    blocked_stages = (
        tuple(stage.stage_id for stage in workflow.stages if stage.status == "blocked")
        if workflow is not None
        else ()
    )
    unmet = (
        tuple(
            stage.description
            for stage in workflow.stages
            if stage.status in {"pending", "in_progress", "blocked"}
        )
        if workflow is not None
        else ()
    )
    return CompletionEvidence(
        request_number,
        tuple(changed[:50]),
        tuple(checks[:20]),
        tuple(sources[:20]),
        tuple(dict.fromkeys(limitations))[:20],
        workflow.workflow_id if workflow is not None else "",
        completed_stages,
        blocked_stages,
        unmet[:20],
    )


def append_verification(answer: str, evidence: CompletionEvidence) -> str:
    """Append an authoritative verification section when work needs evidence."""
    if not evidence.changed_files and not evidence.checks:
        return answer
    lines = [answer.rstrip(), "", "## Verification", ""]
    if evidence.changed_files:
        files = ", ".join(f"`{path}`" for path in evidence.changed_files)
        lines.append(f"- Changed files: {files}")
    else:
        lines.append("- Changed files: None recorded")
    if evidence.checks:
        lines.append(f"- Checks: {'; '.join(evidence.checks)}")
    else:
        lines.append("- Checks: Not run")
    if evidence.limitations:
        lines.append(f"- Unverified or unresolved: {'; '.join(evidence.limitations)}")
    if evidence.unmet_requirements:
        lines.append(
            f"- Workflow requirements not completed: {'; '.join(evidence.unmet_requirements)}"
        )
    return "\n".join(lines)


def enforce_evidence_consistency(answer: str, evidence: CompletionEvidence) -> str:
    """Remove narrow successful-check claims when no successful check was recorded."""
    successful = any(
        check.casefold().endswith(("completed", "passed", "success")) for check in evidence.checks
    )
    if successful:
        return answer
    return re.sub(
        r"\b(?:all\s+)?(?:tests?|lint|type[ -]?checks?|build)\s+(?:have\s+)?passed\b",
        "check success was not verified",
        answer,
        flags=re.IGNORECASE,
    )


def _payload(content: str | None) -> dict[str, object] | None:
    try:
        value = json.loads(content or "")
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
