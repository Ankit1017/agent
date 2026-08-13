"""Tests for model-facing tool adapters and approvals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from local_harness.domain.models import ApprovalDecision, CommandExecution
from local_harness.guardrails.path_policy import WorkspacePathPolicy
from local_harness.infrastructure.filesystem import WorkspaceInspector
from local_harness.infrastructure.tools import (
    ListDirectoryTool,
    ReadFileTool,
    RunPowerShellTool,
    SearchTextTool,
)


@dataclass
class FakeApproval:
    """Return one configured decision."""

    decision: ApprovalDecision

    def request(self, command: str, explanation: str, workspace: str) -> ApprovalDecision:
        """Return the configured decision."""
        return self.decision


@dataclass
class FakeExecutor:
    """Capture approved commands."""

    command: str = ""

    def execute(self, command: str) -> CommandExecution:
        """Record and report successful execution."""
        self.command = command
        return CommandExecution("completed", 0, "ok")


def test_inspection_tools_expose_schemas_and_safe_errors(tmp_path: Path) -> None:
    """Read-only tools delegate valid inputs and serialize expected failures."""
    (tmp_path / "a.txt").write_text("needle", encoding="utf-8")
    inspector = WorkspaceInspector(WorkspacePathPolicy(tmp_path), max_output_chars=1_000)
    listing = ListDirectoryTool(inspector)
    reader = ReadFileTool(inspector)
    search = SearchTextTool(inspector)

    assert listing.definition.name == "list_directory"
    assert "a.txt" in listing.execute({"path": "."}).content
    assert "needle" in reader.execute({"path": "a.txt"}).content
    assert "a.txt:1" in search.execute({"query": "needle"}).content
    assert reader.execute({"path": 3}).is_error
    assert search.execute({"query": "", "path": "."}).is_error


def test_powershell_tool_requires_approval_and_records_result() -> None:
    """Only approved commands reach the executor."""
    executor = FakeExecutor()
    rejected = RunPowerShellTool(
        executor, FakeApproval(ApprovalDecision(False, "use another command")), "C:\\work"
    )
    result = rejected.execute({"command": "Get-Date", "explanation": "show time"})
    assert result.is_error
    assert "use another command" in result.content
    assert executor.command == ""

    approved = RunPowerShellTool(executor, FakeApproval(ApprovalDecision(True)), "C:\\work")
    result = approved.execute({"command": "Get-Date", "explanation": "show time"})
    assert not result.is_error
    assert json.loads(result.content)["approval"] == "approved"
    assert json.loads(result.content)["exit_code"] == 0
    assert executor.command == "Get-Date"


def test_powershell_tool_blocks_policy_and_bad_arguments() -> None:
    """Blocked and malformed commands never request execution."""
    executor = FakeExecutor()
    tool = RunPowerShellTool(executor, FakeApproval(ApprovalDecision(True)), "C:\\work")

    assert tool.execute({"command": "Format-Volume C", "explanation": "bad"}).is_error
    assert tool.execute({"command": 1, "explanation": "bad"}).is_error
    assert executor.command == ""
