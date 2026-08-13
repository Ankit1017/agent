"""Focused tests for compact coding tools and transactional edits."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from local_harness.domain.models import ApprovalDecision, CommandExecution
from local_harness.guardrails.path_policy import WorkspacePathPolicy
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.code_search import CodeFinder
from local_harness.infrastructure.coding_tools import (
    ApplyPatchTool,
    FindCodeTool,
    InspectProjectTool,
    ReadFilesTool,
    RunProjectChecksTool,
)
from local_harness.infrastructure.patching import WorkspacePatchService
from local_harness.infrastructure.project_inspection import (
    BatchFileReader,
    CheckProfileDetector,
    ProjectInspector,
)
from local_harness.infrastructure.tool_output import tool_envelope


class ApprovalFake:
    """Record command and patch approval requests."""

    def __init__(self, approved: bool) -> None:
        """Choose the decision returned for every request."""
        self.approved = approved
        self.previews: list[str] = []
        self.commands: list[str] = []

    def request_patch(self, preview: str, explanation: str, workspace: str) -> ApprovalDecision:
        """Capture a patch preview."""
        self.previews.append(preview)
        return ApprovalDecision(self.approved, "keep it" if not self.approved else "")

    def request(self, command: str, explanation: str, workspace: str) -> ApprovalDecision:
        """Capture an exact check command."""
        self.commands.append(command)
        return ApprovalDecision(self.approved)


class ExecutorFake:
    """Return one deterministic project-check result."""

    def execute(self, command: str) -> CommandExecution:
        """Return successful bounded output."""
        return CommandExecution("completed", 0, "all good")


def test_project_inspection_and_batch_read_are_compact(tmp_path: Path) -> None:
    """Inspection detects metadata while range reads include hashes and mixed boundaries."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
    (tmp_path / "main.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    policy = WorkspacePathPolicy(tmp_path)

    inspected = ProjectInspector(policy).inspect(".", 2)
    read = BatchFileReader(policy).read("main.py", 2, 3)

    assert inspected["languages"] == [{"name": "Python", "files": 1}]
    tree = cast(list[str], inspected["tree"])
    profiles = cast(list[dict[str, object]], inspected["check_profiles"])
    assert ".env" not in tree
    assert "tests" in {item["name"] for item in profiles}
    assert read["content"] == "2: two\n3: three"
    assert read["sha256"] == hashlib.sha256((tmp_path / "main.py").read_bytes()).hexdigest()


def test_code_finder_supports_syntax_and_text_fallback(tmp_path: Path) -> None:
    """Definitions use Tree-sitter and unsupported text remains searchable."""
    (tmp_path / "main.py").write_text("def build_plan():\n    return 1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("build_plan is documented\n", encoding="utf-8")
    finder = CodeFinder(
        WorkspacePathPolicy(tmp_path), cache_directory=tmp_path / ".harness" / "cache"
    )

    definitions, _, _ = finder.find("build_plan", ".", "definition", ["Python"], 10, None)
    references, _, _ = finder.find("build_plan", ".", "reference", [], 10, None)

    assert definitions[0]["kind"] == "definition"
    assert any(item["path"] == "notes.txt" for item in references)
    finder.clear_cache()


def test_patch_requires_approval_and_applies_exact_transaction(tmp_path: Path) -> None:
    """Rejected patches do nothing and approved replacements expose the exact relative diff."""
    target = tmp_path / "nested" / "app.py"
    target.parent.mkdir()
    target.write_text("old\n", encoding="utf-8")
    policy = WorkspacePathPolicy(tmp_path)
    rejected = ApprovalFake(False)
    service = WorkspacePatchService(
        policy,
        rejected,
        SecretRedactor(),
        max_patch_chars=10_000,
        max_output_chars=10_000,
    )
    change = [{"action": "replace", "path": "nested/app.py", "old_text": "old", "new_text": "new"}]

    rejected_result = service.apply(change, "Update value")
    assert rejected_result.is_error
    assert target.read_text(encoding="utf-8") == "old\n"
    assert "a/nested/app.py" in rejected.previews[0]

    approved = ApprovalFake(True)
    service = WorkspacePatchService(
        policy,
        approved,
        SecretRedactor(),
        max_patch_chars=10_000,
        max_output_chars=10_000,
    )
    result = service.apply(change, "Update value")

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "new\n"
    assert json.loads(result.content)["version"] == 1


def test_patch_rejects_protected_and_stale_delete(tmp_path: Path) -> None:
    """Protected targets and incorrect delete hashes fail before approval."""
    (tmp_path / ".env").write_text("secret", encoding="utf-8")
    target = tmp_path / "file.txt"
    target.write_text("data", encoding="utf-8")
    approval = ApprovalFake(True)
    service = WorkspacePatchService(
        WorkspacePathPolicy(tmp_path),
        approval,
        SecretRedactor(),
        max_patch_chars=10_000,
        max_output_chars=10_000,
    )

    protected = service.apply([{"action": "delete", "path": ".env", "expected_sha256": "x"}], "x")
    stale = service.apply([{"action": "delete", "path": "file.txt", "expected_sha256": "x"}], "x")

    assert protected.is_error and stale.is_error
    assert target.exists()
    assert approval.previews == []


def test_detected_project_check_requires_exact_approval(tmp_path: Path) -> None:
    """Only detected profiles execute and their exact command is approved each time."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    policy = WorkspacePathPolicy(tmp_path)
    approval = ApprovalFake(True)
    tool = RunProjectChecksTool(
        CheckProfileDetector(policy),
        ExecutorFake(),
        approval,
        str(tmp_path),
        SecretRedactor(),
        max_output_chars=5_000,
    )

    result = tool.execute({"profile": "tests", "explanation": "Verify behavior"})
    unknown = tool.execute({"profile": "format", "explanation": "Change formatting"})

    assert not result.is_error
    assert approval.commands == ["python -m pytest"]
    assert unknown.is_error


def test_v2_tool_adapters_return_bounded_envelopes_and_partial_errors(tmp_path: Path) -> None:
    """Public adapters validate arguments and keep useful records when one batch item fails."""
    (tmp_path / "app.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    policy = WorkspacePathPolicy(tmp_path)
    redactor = SecretRedactor(("secret-value",))
    finder = CodeFinder(policy, cache_directory=tmp_path / ".harness" / "cache")
    inspect_tool = InspectProjectTool(ProjectInspector(policy), redactor, max_output_chars=8_000)
    read_tool = ReadFilesTool(
        BatchFileReader(policy), redactor, max_files=8, max_output_chars=8_000
    )
    find_tool = FindCodeTool(finder, redactor, max_output_chars=8_000)

    inspected = inspect_tool.execute({"path": ".", "depth": 2})
    read = read_tool.execute(
        {
            "requests": [
                {"path": "app.py", "start_line": 1, "end_line": 2},
                {"path": ".env"},
            ]
        }
    )
    found = find_tool.execute(
        {"query": "hello", "kind": "definition", "languages": ["Python"], "limit": 10}
    )
    invalid = find_tool.execute({"query": "hello", "languages": "Python"})

    assert json.loads(inspected.content)["version"] == 1
    assert json.loads(read.content)["metadata"]["failures"] == 1
    assert not read.is_error
    assert json.loads(found.content)["items"][0]["name"] == "hello"
    assert invalid.is_error


def test_apply_patch_tool_validates_shape_and_delegates(tmp_path: Path) -> None:
    """The model adapter rejects malformed operations and delegates valid requests."""
    approval = ApprovalFake(True)
    service = WorkspacePatchService(
        WorkspacePathPolicy(tmp_path),
        approval,
        SecretRedactor(),
        max_patch_chars=10_000,
        max_output_chars=10_000,
    )
    tool = ApplyPatchTool(service)

    malformed = tool.execute({"changes": "bad", "explanation": "x"})
    missing_explanation = tool.execute({"changes": [{"action": "create", "path": "x.txt"}]})
    created = tool.execute(
        {
            "changes": [{"action": "create", "path": "x.txt", "content": "hello"}],
            "explanation": "Create file",
        }
    )

    assert malformed.is_error and missing_explanation.is_error
    assert not created.is_error
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "hello"


def test_tool_envelope_truncates_items_as_valid_redacted_json() -> None:
    """Oversized result tails are removed without producing invalid JSON or leaked secrets."""
    rendered = tool_envelope(
        "Contains secret-value",
        [{"value": "x" * 900}, {"value": "y" * 900}],
        max_chars=300,
        redactor=SecretRedactor(("secret-value",)),
    )

    payload = json.loads(rendered)
    assert payload["truncated"] is True
    assert "secret-value" not in rendered
