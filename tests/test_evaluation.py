"""Tests for harness evaluation evidence and controlled candidate proposals."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import local_harness.interfaces.cli as cli
import local_harness.interfaces.eval_cli as eval_cli
from local_harness.application.agent import AgentService
from local_harness.application.evaluation import EvaluationService
from local_harness.application.evaluation_components import component_snapshots
from local_harness.application.workflows import (
    WorkflowCatalog,
    WorkflowCoordinator,
    WorkflowSelector,
)
from local_harness.bootstrap import Runtime
from local_harness.domain.errors import SessionError, ToolExecutionError
from local_harness.domain.evaluation import EvaluationObservation, EvaluationRun, EvaluationScore
from local_harness.domain.models import CompletionEvidence, Message, ProgressEvent, Session
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.evaluation_store import SqliteEvaluationRepository
from local_harness.interfaces.commands import InterfaceCommand


class ProposalModel:
    """Return one deterministic structured candidate proposal."""

    def complete(self, messages: object, tools: object) -> Message:
        """Return JSON without using network access."""
        del messages, tools
        return Message(
            "assistant",
            json.dumps(
                {
                    "proposal": "Prefer project memory before repeated reads",
                    "predicted_changes": ["tokens -12%"],
                    "evidence_ids": ["obs-1"],
                    "risks": ["stale retrieval"],
                    "rollback_instructions": "Restore the prior routing rule",
                    "required_suite": "core",
                }
            ),
        )


class MalformedProposalModel:
    """Return an invalid candidate payload."""

    def complete(self, messages: object, tools: object) -> Message:
        """Return malformed JSON without using network access."""
        del messages, tools
        return Message("assistant", "not-json")


def _service(
    tmp_path: Path, *, minimum: int = 10
) -> tuple[EvaluationService, SqliteEvaluationRepository]:
    redactor = SecretRedactor(("super-secret",))
    repository = SqliteEvaluationRepository(tmp_path, redactor)
    catalog = WorkflowCatalog()
    service = EvaluationService(
        repository,
        workspace_identity=str(tmp_path.resolve()),
        harness_revision="a" * 40,
        component_snapshots=component_snapshots(
            "system",
            catalog,
            ("project_memory", "read_files"),
            tool_profile="auto",
            schema_limit=8,
            activation_limit=5,
            context_max_chars=30_000,
            retrieval_max_files=6,
            retrieval_max_chars=12_000,
        ),
        selector=WorkflowSelector(catalog),
        sanitizer=redactor.redact,
        min_comparison_cases=minimum,
    )
    return service, repository


def test_contract_observation_handoff_and_redaction_round_trip(tmp_path: Path) -> None:
    """A completed request produces durable redacted contract, metrics, and handoff."""
    service, repository = _service(tmp_path)
    session = Session("a" * 32, str(tmp_path), "model")
    catalog = WorkflowCatalog()
    definition = catalog.get("web_research")
    coordinator = WorkflowCoordinator(session, catalog)
    coordinator.start(1, WorkflowSelector(catalog).select("anything", "web_research"))
    coordinator.after_tool("web_search", is_error=False, summary="Found sources")
    coordinator.after_tool("read_web_pages", is_error=False, summary="Read sources")
    session.messages.extend(
        [
            Message("user", "Research super-secret", request_number=1),
            Message("assistant", "Answer with source", request_number=1),
        ]
    )
    session.events.extend(
        [
            ProgressEvent(
                1,
                1,
                "model_complete",
                "Answered",
                "final",
                "success",
                duration_ms=20,
                request_number=1,
                input_tokens=10,
                output_tokens=5,
            ),
            ProgressEvent(
                2,
                1,
                "tool_complete",
                "Read source",
                "read_web_pages",
                "success",
                duration_ms=5,
                request_number=1,
            ),
        ]
    )
    session.evidence.append(CompletionEvidence(1, sources=("https://example.com",)))

    contract = service.begin_request(
        session,
        1,
        "Research super-secret",
        definition,
        max_llm_calls=10,
        max_tokens=0,
        max_context_chars=30_000,
        max_runtime_ms=120_000,
    )
    observation = service.complete_request(session, 1)

    assert contract is not None and "super-secret" not in contract.prompt_fingerprint
    assert observation is not None and observation.score.outcome == "pass"
    assert repository.get_observation(session.session_id, 1) == observation
    handoff = service.handoff(session.session_id)
    assert handoff is not None and handoff.next_action
    assert "super-secret" not in (
        tmp_path / ".harness/evaluations/evaluations.sqlite3"
    ).read_bytes().decode("utf-8", errors="ignore")


def test_offline_suite_covers_all_workflows_without_model_calls(tmp_path: Path) -> None:
    """The deterministic core suite evaluates one fixture per built-in workflow."""
    service, _ = _service(tmp_path)

    run = service.run_suite("core")

    assert run.status == "completed"
    assert len(run.case_ids) == 20
    assert all(item.score.llm_calls == 0 for item in service.history(20))
    with pytest.raises(ToolExecutionError, match="Live evaluation"):
        service.run_suite("core", live=True)


def test_candidate_proposal_is_structured_approved_and_never_applied(tmp_path: Path) -> None:
    """Proposal calls persist bounded records and decisions without source mutation."""
    service, _ = _service(tmp_path)

    candidate = service.propose(ProposalModel(), "tool_profiles")
    approved = service.decide_candidate(candidate.candidate_id, True)

    assert candidate.component_ids == ("tool_profiles",)
    assert approved.status == "approved"
    assert service.candidate(candidate.candidate_id) == approved
    assert service.candidates()[0] == approved
    with pytest.raises(ToolExecutionError, match="Unknown candidate component"):
        service.propose(ProposalModel(), "source_code")


def test_comparison_thresholds_and_user_marks(tmp_path: Path) -> None:
    """Paired comparisons enforce quality gates and efficiency thresholds."""
    service, repository = _service(tmp_path, minimum=2)
    baseline_ids: list[str] = []
    candidate_ids: list[str] = []
    for index in range(2):
        base = _observation(f"base-{index}", f"case-{index}", tokens=100, runtime=100)
        candidate = _observation(f"candidate-{index}", f"case-{index}", tokens=80, runtime=80)
        repository.save_observation(base)
        repository.save_observation(candidate)
        baseline_ids.append(base.observation_id)
        candidate_ids.append(candidate.observation_id)
    repository.save_run(_run("baseline", baseline_ids))
    repository.save_run(_run("candidate", candidate_ids))

    comparison = service.compare("baseline", "candidate")
    marked = service.mark("session", 1, "pass", "looks good")

    assert comparison.verdict == "better"
    assert comparison.token_delta_percent == -20.0
    assert marked.user_mark == "pass"
    with pytest.raises(SessionError, match="pass or fail"):
        repository.mark_observation("session", 1, "unknown", "")


def test_corrupt_evaluation_database_is_quarantined(tmp_path: Path) -> None:
    """Corrupt evaluation storage is recoverable workspace-local data."""
    root = tmp_path / ".harness" / "evaluations"
    root.mkdir(parents=True)
    (root / "evaluations.sqlite3").write_bytes(b"not sqlite")

    repository = SqliteEvaluationRepository(tmp_path, SecretRedactor())

    assert repository.list_observations() == ()
    assert list(root.glob("evaluations.corrupt-*.sqlite3"))


def test_disabled_capture_failures_and_candidate_validation(tmp_path: Path) -> None:
    """Disabled capture, missing records, malformed proposals, and live mode fail safely."""
    service, repository = _service(tmp_path)
    session = Session("a" * 32, str(tmp_path), "model")
    service._capture_sessions = False
    assert (
        service.begin_request(
            session,
            1,
            "hello",
            None,
            max_llm_calls=1,
            max_tokens=0,
            max_context_chars=100,
            max_runtime_ms=100,
        )
        is None
    )
    assert service.complete_request(session, 1) is None
    assert service.status()["observations"] == 0
    assert service.handoff(session.session_id) is None
    with pytest.raises(SessionError, match="not found"):
        service.compare("missing", "also-missing")
    with pytest.raises(SessionError, match="not found"):
        service.candidate("missing")
    with pytest.raises(SessionError, match="between 1 and 1000"):
        repository.list_observations(0)
    with pytest.raises(ToolExecutionError, match="Unknown evaluation suite"):
        service.run_suite("missing")

    service._live_enabled = True
    with pytest.raises(ToolExecutionError, match="submitted individually"):
        service.run_suite("core", live=True)
    service._candidates_enabled = False
    with pytest.raises(ToolExecutionError, match="disabled"):
        service.propose(ProposalModel())

    service._candidates_enabled = True
    with pytest.raises(ToolExecutionError, match="structured JSON"):
        service.propose(MalformedProposalModel())


def test_comparison_insufficient_mixed_and_regression(tmp_path: Path) -> None:
    """Comparison distinguishes insufficient, mixed, and guardrail-regressing results."""
    service, repository = _service(tmp_path, minimum=2)
    repository.save_observation(_observation("one-base", "case-0", tokens=100, runtime=100))
    repository.save_observation(_observation("one-new", "case-0", tokens=100, runtime=100))
    repository.save_run(_run("one-left", ["one-base"]))
    repository.save_run(_run("one-right", ["one-new"]))
    assert service.compare("one-left", "one-right").verdict == "insufficient_evidence"

    for index in range(2):
        left = _observation(f"mixed-left-{index}", f"mixed-{index}", tokens=100, runtime=100)
        right = _observation(f"mixed-right-{index}", f"mixed-{index}", tokens=100, runtime=100)
        repository.save_observation(left)
        repository.save_observation(right)
    repository.save_run(_run("mixed-left", ["mixed-left-0", "mixed-left-1"]))
    repository.save_run(_run("mixed-right", ["mixed-right-0", "mixed-right-1"]))
    assert service.compare("mixed-left", "mixed-right").verdict == "mixed"

    unsafe = _observation("unsafe", "mixed-0", tokens=90, runtime=90)
    unsafe = replace(unsafe, score=replace(unsafe.score, policy_events=1))
    repository.save_observation(unsafe)
    repository.save_run(_run("unsafe-run", ["unsafe", "mixed-right-1"]))
    assert service.compare("mixed-left", "unsafe-run").verdict == "worse"


def test_plain_interface_exposes_evaluation_and_candidate_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Plain commands share the evaluation service and never apply candidate source changes."""
    service, _ = _service(tmp_path)
    session = Session("a" * 32, str(tmp_path), "model")
    session.messages.append(Message("assistant", "answer", request_number=1))
    service.begin_request(
        session,
        1,
        "hello",
        None,
        max_llm_calls=2,
        max_tokens=0,
        max_context_chars=1000,
        max_runtime_ms=1000,
    )
    service.complete_request(session, 1)
    candidate = service.propose(ProposalModel(), "tool_profiles")

    agent = SimpleNamespace(session=session, next_request_number=2)
    runtime = SimpleNamespace(evaluation=service, model_client=ProposalModel())
    commands = (
        InterfaceCommand("eval", "status"),
        InterfaceCommand("eval", "contract 1"),
        InterfaceCommand("eval", "mark pass reviewed"),
        InterfaceCommand("eval", "history 2"),
        InterfaceCommand("eval", "run core"),
        InterfaceCommand("eval", "compare missing absent"),
        InterfaceCommand("eval", "unknown"),
        InterfaceCommand("handoff", ""),
        InterfaceCommand("candidate", f"show {candidate.candidate_id}"),
        InterfaceCommand("candidate", f"approve {candidate.candidate_id}"),
        InterfaceCommand("candidate", f"reject {candidate.candidate_id} no"),
        InterfaceCommand("candidate", "propose system_prompt"),
        InterfaceCommand("candidate", "bad"),
    )
    for command in commands:
        cli._handle_plain_command(cast(Runtime, runtime), cast(AgentService, agent), command)

    output = capsys.readouterr().out
    assert "component_fingerprint" in output
    assert "Marked request 1 as pass" in output
    assert "No handoff" not in output
    assert "Usage: /eval" in output
    assert "Usage: /candidate" in output


def test_harness_eval_entry_point_runs_and_translates_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The scripted entry point runs offline and reports invalid comparisons cleanly."""
    service, _ = _service(tmp_path)
    runtime = SimpleNamespace(evaluation=service)
    monkeypatch.setattr(eval_cli, "build_runtime", lambda workspace: runtime)

    eval_cli.main(["--workspace", str(tmp_path), "--suite", "core"])
    assert '"status": "completed"' in capsys.readouterr().out

    with pytest.raises(SystemExit):
        eval_cli.main(["--workspace", str(tmp_path), "--compare", "missing", "absent"])
    assert "Evaluation run was not found" in capsys.readouterr().err

    runtime.evaluation = None
    with pytest.raises(SystemExit):
        eval_cli.main(["--workspace", str(tmp_path)])
    assert "Evaluation is disabled" in capsys.readouterr().err


def _observation(
    observation_id: str, case_id: str, *, tokens: int, runtime: int
) -> EvaluationObservation:
    score = EvaluationScore(
        "pass", True, True, True, True, True, 2, tokens, 0, tokens * 4, 0, 0, 0, 0, 0, runtime
    )
    return EvaluationObservation(
        observation_id,
        "contract",
        "workspace",
        "session",
        1,
        "model",
        1,
        "b" * 40,
        case_id,
        score,
        (),
        (),
        (),
        created_at="2026-01-01T00:00:00+00:00",
    )


def _run(run_id: str, observation_ids: list[str]) -> EvaluationRun:
    return EvaluationRun(
        run_id,
        "core",
        "model",
        "fingerprint",
        False,
        ("case-0", "case-1"),
        tuple(observation_ids),
        "completed",
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:01:00+00:00",
    )
