"""Provider-neutral entities for harness evaluation and controlled evolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

EvaluationOutcome = Literal["pass", "fail", "unknown"]
ComparisonVerdict = Literal["better", "mixed", "worse", "insufficient_evidence"]
CandidateStatus = Literal["proposed", "approved", "rejected"]


@dataclass(frozen=True, slots=True)
class EvaluationContract:
    """A falsifiable declaration of one request's expected observable outcome."""

    contract_id: str
    session_id: str
    request_number: int
    expected_workflow: str
    required_tool_groups: tuple[tuple[str, ...], ...]
    require_checks: bool
    require_sources: bool
    require_changes: bool
    max_llm_calls: int
    max_tokens: int
    max_context_chars: int
    max_runtime_ms: int
    expected_outcome: str
    prompt_fingerprint: str
    component_fingerprint: str
    created_at: str


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One versioned repeatable evaluation input and its assertions."""

    case_id: str
    version: int
    suite: str
    prompt: str
    expected_workflow: str
    expected_tools: tuple[str, ...] = ()
    mutation: bool = False


@dataclass(frozen=True, slots=True)
class EvaluationScore:
    """Deterministic quality and efficiency measurements for one observation."""

    outcome: EvaluationOutcome
    verified: bool
    workflow_correct: bool
    required_stages_complete: bool
    answer_quality_passed: bool
    citation_validation_passed: bool
    llm_calls: int
    input_tokens: int
    output_tokens: int
    context_chars: int
    context_chars_saved: int
    tool_failures: int
    repeated_calls_blocked: int
    policy_events: int
    approval_rejections: int
    runtime_ms: int


@dataclass(frozen=True, slots=True)
class EvaluationObservation:
    """A redacted request outcome linked to its contract and execution evidence."""

    observation_id: str
    contract_id: str
    workspace_identity: str
    session_id: str
    request_number: int
    model: str
    workflow_version: int
    harness_revision: str
    case_id: str | None
    score: EvaluationScore
    completed: tuple[str, ...]
    failures: tuple[str, ...]
    evidence_sequences: tuple[int, ...]
    user_mark: EvaluationOutcome = "unknown"
    user_note: str = ""
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    """A named collection of observations produced from one evaluation suite."""

    run_id: str
    suite: str
    model: str
    component_fingerprint: str
    live: bool
    case_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    status: Literal["running", "completed", "failed"]
    started_at: str
    completed_at: str = ""


@dataclass(frozen=True, slots=True)
class CandidateComparison:
    """A paired baseline-versus-candidate metric comparison."""

    comparison_id: str
    baseline_run_id: str
    candidate_run_id: str
    paired_cases: int
    verdict: ComparisonVerdict
    pass_rate_delta_points: float
    token_delta_percent: float
    call_delta_percent: float
    latency_delta_percent: float
    guardrail_regression: bool
    verification_regression: bool
    summary: str
    created_at: str


@dataclass(frozen=True, slots=True)
class HarnessCandidate:
    """A bounded proposal that cannot directly modify harness source code."""

    candidate_id: str
    component_ids: tuple[str, ...]
    proposal: str
    predicted_changes: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    risks: tuple[str, ...]
    rollback_instructions: str
    required_suite: str
    status: CandidateStatus = "proposed"
    feedback: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class HandoffSnapshot:
    """A concise cross-session record of completed and remaining observable work."""

    session_id: str
    request_number: int
    completed: tuple[str, ...]
    remaining: tuple[str, ...]
    failures: tuple[str, ...]
    changed_files: tuple[str, ...]
    checks: tuple[str, ...]
    next_action: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ComponentSnapshot:
    """A stable fingerprint of one candidate-editable harness component."""

    component_id: str
    description: str
    source_hash: str
    configuration: str
    evaluation_ids: tuple[str, ...] = field(default_factory=tuple)
