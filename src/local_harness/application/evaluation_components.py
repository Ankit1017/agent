"""Stable observable component snapshots for controlled harness proposals."""

from __future__ import annotations

import hashlib
import json

from local_harness.application.workflows import WorkflowCatalog
from local_harness.domain.evaluation import ComponentSnapshot


def component_snapshots(
    system_prompt: str,
    workflow_catalog: WorkflowCatalog,
    tool_names: tuple[str, ...],
    *,
    tool_profile: str,
    schema_limit: int,
    activation_limit: int,
    context_max_chars: int,
    retrieval_max_files: int,
    retrieval_max_chars: int,
) -> tuple[ComponentSnapshot, ...]:
    """Return the four allowlisted proposal surfaces with deterministic hashes."""
    workflows = [
        {
            "workflow_id": item.workflow_id,
            "triggers": item.triggers,
            "negative_triggers": item.negative_triggers,
            "priority": item.priority,
        }
        for item in workflow_catalog.list()
        if item.workflow_id != "general_assistance"
    ]
    values = (
        ("system_prompt", "Provider instructions", system_prompt),
        (
            "workflow_selector",
            "Workflow triggers, negative triggers, and priorities",
            json.dumps(workflows, ensure_ascii=False, sort_keys=True),
        ),
        (
            "tool_profiles",
            "Registered tool visibility and deferred-routing limits",
            json.dumps(
                {
                    "tool_names": tool_names,
                    "configured_profile": tool_profile,
                    "schema_limit": schema_limit,
                    "activation_limit": activation_limit,
                },
                sort_keys=True,
            ),
        ),
        (
            "context_budgets",
            "Provider context and project-memory retrieval budgets",
            json.dumps(
                {
                    "context_max_chars": context_max_chars,
                    "retrieval_max_files": retrieval_max_files,
                    "retrieval_max_chars": retrieval_max_chars,
                },
                sort_keys=True,
            ),
        ),
    )
    return tuple(
        ComponentSnapshot(
            component_id=component_id,
            description=description,
            source_hash=hashlib.sha256(configuration.encode("utf-8")).hexdigest(),
            configuration=configuration[:20_000],
        )
        for component_id, description, configuration in values
    )
