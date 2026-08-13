"""Built-in deterministic fixtures for situation-workflow evaluation."""

from __future__ import annotations

from local_harness.application.workflows import WORKFLOWS
from local_harness.domain.evaluation import EvaluationCase


def built_in_cases() -> tuple[EvaluationCase, ...]:
    """Return one stable routing fixture for every specialized workflow."""
    return tuple(
        EvaluationCase(
            case_id=f"workflow-{workflow.workflow_id}",
            version=1,
            suite="core",
            prompt=_fixture_prompt(workflow.workflow_id, workflow.triggers[0]),
            expected_workflow=workflow.workflow_id,
            expected_tools=tuple(
                dict.fromkeys(tool for stage in workflow.stages for tool in stage.tools)
            ),
            mutation=workflow.completion.require_changed_files,
        )
        for workflow in WORKFLOWS
    )


def _fixture_prompt(workflow_id: str, trigger: str) -> str:
    overrides = {
        "diagnose_bug": "Diagnose this bug and find the root cause without editing files",
        "configuration_change": "Change this configuration safely and verify it",
        "documentation_update": "Update documentation to match the implementation",
        "api_integration": "Integrate this external API using official documentation",
        "windows_troubleshooting": "Troubleshoot this Windows PowerShell setup",
    }
    return overrides.get(workflow_id, f"Please {trigger} for this project")
