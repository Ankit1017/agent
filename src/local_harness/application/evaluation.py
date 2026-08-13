"""Deterministic evaluation, comparison, handoff, and candidate-proposal use cases."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Literal

from local_harness.application.evaluation_cases import built_in_cases
from local_harness.application.ports import EvaluationRepository, ModelClient
from local_harness.application.workflows import WorkflowSelector
from local_harness.domain.errors import SessionError, ToolExecutionError
from local_harness.domain.evaluation import (
    CandidateComparison,
    ComponentSnapshot,
    EvaluationContract,
    EvaluationObservation,
    EvaluationRun,
    EvaluationScore,
    HandoffSnapshot,
    HarnessCandidate,
)
from local_harness.domain.models import Message, Session, WorkflowDefinition
from local_harness.identifiers import new_session_id


class EvaluationService:
    """Capture request evidence and compare harness behavior without modifying source."""

    def __init__(
        self,
        repository: EvaluationRepository,
        *,
        workspace_identity: str,
        harness_revision: str,
        component_snapshots: tuple[ComponentSnapshot, ...],
        selector: WorkflowSelector,
        sanitizer: Callable[[str], str],
        max_trace_chars: int = 30_000,
        min_comparison_cases: int = 10,
        capture_sessions: bool = True,
        candidates_enabled: bool = True,
        live_enabled: bool = False,
    ) -> None:
        """Configure bounded workspace-local evaluation behavior."""
        self._repository = repository
        self._workspace_identity = workspace_identity
        self._revision = harness_revision
        self._components = {item.component_id: item for item in component_snapshots}
        self._selector = selector
        self._sanitize = sanitizer
        self._max_trace_chars = max_trace_chars
        self._minimum_cases = min_comparison_cases
        self._capture_sessions = capture_sessions
        self._candidates_enabled = candidates_enabled
        self._live_enabled = live_enabled
        self._component_fingerprint = _fingerprint(
            json.dumps([asdict(item) for item in component_snapshots], sort_keys=True)
        )

    @property
    def enabled(self) -> bool:
        """Return whether ordinary sessions are captured automatically."""
        return self._capture_sessions

    @property
    def component_fingerprint(self) -> str:
        """Return the frozen editable-component fingerprint."""
        return self._component_fingerprint

    def begin_request(
        self,
        session: Session,
        request_number: int,
        prompt: str,
        workflow: WorkflowDefinition | None,
        *,
        max_llm_calls: int,
        max_tokens: int,
        max_context_chars: int,
        max_runtime_ms: int,
    ) -> EvaluationContract | None:
        """Persist a falsifiable request contract before the first provider call."""
        if not self._capture_sessions:
            return None
        completion = workflow.completion if workflow is not None else None
        required_groups = (
            tuple(stage.tools for stage in workflow.stages if stage.required)
            if workflow is not None
            else ()
        )
        contract = EvaluationContract(
            contract_id=new_session_id(),
            session_id=session.session_id,
            request_number=request_number,
            expected_workflow=workflow.workflow_id
            if workflow is not None
            else "general_assistance",
            required_tool_groups=required_groups,
            require_checks=bool(completion and completion.require_successful_check),
            require_sources=bool(completion and completion.require_sources),
            require_changes=bool(completion and completion.require_changed_files),
            max_llm_calls=max_llm_calls,
            max_tokens=max_tokens,
            max_context_chars=max_context_chars,
            max_runtime_ms=max_runtime_ms,
            expected_outcome="Substantive answer consistent with recorded verification evidence",
            prompt_fingerprint=_fingerprint(self._sanitize(prompt)),
            component_fingerprint=self._component_fingerprint,
            created_at=_now(),
        )
        self._repository.save_contract(contract)
        return contract

    def complete_request(
        self, session: Session, request_number: int
    ) -> EvaluationObservation | None:
        """Derive and persist a redacted observation and handoff from session evidence."""
        contract = self._repository.get_contract(session.session_id, request_number)
        if contract is None:
            return None
        events = [item for item in session.events if item.request_number == request_number]
        evidence = next(
            (item for item in session.evidence if item.request_number == request_number), None
        )
        workflow = next(
            (item for item in session.workflows if item.request_number == request_number), None
        )
        assistant = next(
            (
                item
                for item in reversed(session.messages)
                if item.request_number == request_number
                and item.role == "assistant"
                and item.content
            ),
            None,
        )
        failures = tuple(
            self._sanitize(item.summary)[:300]
            for item in events
            if item.status in {"error", "warning"}
        )
        completed = tuple(
            self._sanitize(item.summary)[:300]
            for item in events
            if item.status == "success" and item.kind in {"tool_complete", "workflow_stage"}
        )
        unmet = evidence.unmet_requirements if evidence is not None else ()
        limitations = evidence.limitations if evidence is not None else ()
        required_complete = bool(workflow is None or workflow.status == "completed") and not unmet
        verified = required_complete and not limitations
        if contract.require_checks:
            verified = verified and bool(evidence and evidence.checks)
        if contract.require_sources:
            verified = verified and bool(evidence and evidence.sources)
        if contract.require_changes:
            verified = verified and bool(evidence and evidence.changed_files)
        answer_quality = bool(
            assistant
            and assistant.content
            and not any(
                item.kind == "model_error" and "quality" in item.summary.casefold()
                for item in events
            )
        )
        citation_quality = not contract.require_sources or bool(evidence and evidence.sources)
        input_tokens = sum(item.input_tokens for item in events)
        output_tokens = sum(item.output_tokens for item in events)
        context_saved = sum(
            _metadata_int(item.metadata.get("schema_chars_saved", 0))
            + _metadata_int(item.metadata.get("candidate_chars_avoided", 0))
            for item in events
        )
        score = EvaluationScore(
            outcome="pass" if verified and answer_quality else "fail",
            verified=verified,
            workflow_correct=bool(
                workflow is None or workflow.workflow_id == contract.expected_workflow
            ),
            required_stages_complete=required_complete,
            answer_quality_passed=answer_quality,
            citation_validation_passed=citation_quality,
            llm_calls=sum(item.kind == "model_complete" for item in events),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            context_chars=(input_tokens + output_tokens) * 4,
            context_chars_saved=context_saved,
            tool_failures=sum(item.kind == "tool_error" for item in events),
            repeated_calls_blocked=sum("repeated" in item.summary.casefold() for item in events),
            policy_events=sum(item.kind == "security_notice" for item in events),
            approval_rejections=sum("reject" in item.summary.casefold() for item in events),
            runtime_ms=sum(item.duration_ms for item in events),
        )
        observation = EvaluationObservation(
            observation_id=new_session_id(),
            contract_id=contract.contract_id,
            workspace_identity=self._workspace_identity,
            session_id=session.session_id,
            request_number=request_number,
            model=session.model,
            workflow_version=workflow.workflow_version if workflow is not None else 0,
            harness_revision=self._revision,
            case_id=None,
            score=score,
            completed=completed,
            failures=tuple((*failures, *unmet, *limitations)),
            evidence_sequences=tuple(item.sequence for item in events)[-200:],
            created_at=_now(),
        )
        self._repository.save_observation(observation)
        snapshot = HandoffSnapshot(
            session_id=session.session_id,
            request_number=request_number,
            completed=completed[-20:],
            remaining=tuple((*unmet, *limitations))[:20],
            failures=failures[-20:],
            changed_files=evidence.changed_files if evidence is not None else (),
            checks=evidence.checks if evidence is not None else (),
            next_action=(
                f"Resolve: {unmet[0]}" if unmet else "Continue with the next requested outcome"
            ),
            created_at=_now(),
        )
        self._repository.save_handoff(snapshot)
        return observation

    def status(self) -> dict[str, object]:
        """Return compact aggregate metrics for interfaces."""
        observations = self._repository.list_observations(1_000)
        count = len(observations)
        passed = sum(item.score.outcome == "pass" for item in observations)
        verified = sum(item.score.verified for item in observations)
        return {
            "enabled": self._capture_sessions,
            "observations": count,
            "pass_rate": round(passed * 100 / count, 2) if count else 0.0,
            "verification_rate": round(verified * 100 / count, 2) if count else 0.0,
            "tokens": sum(
                item.score.input_tokens + item.score.output_tokens for item in observations
            ),
            "llm_calls": sum(item.score.llm_calls for item in observations),
            "component_fingerprint": self._component_fingerprint,
        }

    def history(self, limit: int = 20) -> tuple[EvaluationObservation, ...]:
        """Return recent observations."""
        return self._repository.list_observations(limit)

    def contract(self, session_id: str, request_number: int) -> EvaluationContract | None:
        """Return one request contract."""
        return self._repository.get_contract(session_id, request_number)

    def mark(
        self, session_id: str, request_number: int, outcome: Literal["pass", "fail"], note: str
    ) -> EvaluationObservation:
        """Record an explicit user outcome without rewriting deterministic scores."""
        return self._repository.mark_observation(session_id, request_number, outcome, note)

    def handoff(self, session_id: str) -> HandoffSnapshot | None:
        """Return the latest cross-session handoff snapshot."""
        return self._repository.latest_handoff(session_id)

    def compare(self, baseline_id: str, candidate_id: str) -> CandidateComparison:
        """Compare paired evaluation runs using fixed quality and efficiency thresholds."""
        baseline = self._repository.get_run(baseline_id)
        candidate = self._repository.get_run(candidate_id)
        if baseline is None or candidate is None:
            raise SessionError("Evaluation run was not found")
        observations = {
            item.observation_id: item for item in self._repository.list_observations(1_000)
        }
        left = [observations[item] for item in baseline.observation_ids if item in observations]
        right = [observations[item] for item in candidate.observation_ids if item in observations]
        left_by_case = {item.case_id: item for item in left if item.case_id}
        right_by_case = {item.case_id: item for item in right if item.case_id}
        case_ids = sorted(left_by_case.keys() & right_by_case.keys())
        pairs = [(left_by_case[item], right_by_case[item]) for item in case_ids]
        verdict: Literal["better", "mixed", "worse", "insufficient_evidence"]
        if len(pairs) < self._minimum_cases:
            verdict = "insufficient_evidence"
        pass_delta = _rate_delta(pairs, lambda item: item.score.outcome == "pass")
        token_delta = _median_delta(
            pairs, lambda item: item.score.input_tokens + item.score.output_tokens
        )
        call_delta = _median_delta(pairs, lambda item: item.score.llm_calls)
        latency_delta = _median_delta(pairs, lambda item: item.score.runtime_ms)
        guardrail_regression = any(
            right_item.score.policy_events > left_item.score.policy_events
            for left_item, right_item in pairs
        )
        verification_regression = any(
            left_item.score.verified and not right_item.score.verified
            for left_item, right_item in pairs
        )
        if len(pairs) >= self._minimum_cases:
            if guardrail_regression or verification_regression or pass_delta < -1.0:
                verdict = "worse"
            elif pass_delta >= 2.0 or (
                min(token_delta, call_delta, latency_delta) <= -10.0 and pass_delta >= -1.0
            ):
                verdict = "better"
            else:
                verdict = "mixed"
        comparison = CandidateComparison(
            comparison_id=new_session_id(),
            baseline_run_id=baseline_id,
            candidate_run_id=candidate_id,
            paired_cases=len(pairs),
            verdict=verdict,
            pass_rate_delta_points=pass_delta,
            token_delta_percent=token_delta,
            call_delta_percent=call_delta,
            latency_delta_percent=latency_delta,
            guardrail_regression=guardrail_regression,
            verification_regression=verification_regression,
            summary=(
                f"{verdict}: {len(pairs)} paired cases; pass {pass_delta:+.2f} points; "
                f"tokens {token_delta:+.2f}%"
            ),
            created_at=_now(),
        )
        self._repository.save_comparison(comparison)
        return comparison

    def run_suite(self, suite: str = "core", *, live: bool = False) -> EvaluationRun:
        """Run the deterministic fixture suite; live mutation remains separately approved."""
        if live:
            if not self._live_enabled:
                raise ToolExecutionError("Live evaluation is disabled by configuration")
            raise ToolExecutionError(
                "Live cases must be submitted individually so normal approvals remain authoritative"
            )
        cases = tuple(item for item in built_in_cases() if item.suite == suite)
        if not cases:
            raise ToolExecutionError(f"Unknown evaluation suite: {suite}")
        run_id = new_session_id()
        started = _now()
        observation_ids: list[str] = []
        for index, case in enumerate(cases, 1):
            selection = self._selector.select(case.prompt)
            passed = selection.workflow_id == case.expected_workflow
            score = EvaluationScore(
                outcome="pass" if passed else "fail",
                verified=passed,
                workflow_correct=passed,
                required_stages_complete=passed,
                answer_quality_passed=True,
                citation_validation_passed=True,
                llm_calls=0,
                input_tokens=0,
                output_tokens=0,
                context_chars=0,
                context_chars_saved=0,
                tool_failures=0,
                repeated_calls_blocked=0,
                policy_events=0,
                approval_rejections=0,
                runtime_ms=0,
            )
            observation = EvaluationObservation(
                observation_id=new_session_id(),
                contract_id=f"fixture:{case.case_id}:{case.version}",
                workspace_identity=self._workspace_identity,
                session_id=f"eval:{run_id}",
                request_number=index,
                model="offline-deterministic",
                workflow_version=1,
                harness_revision=self._revision,
                case_id=case.case_id,
                score=score,
                completed=(f"Selected {selection.workflow_id}",),
                failures=() if passed else (f"Expected {case.expected_workflow}",),
                evidence_sequences=(),
                created_at=_now(),
            )
            self._repository.save_observation(observation)
            observation_ids.append(observation.observation_id)
        run = EvaluationRun(
            run_id=run_id,
            suite=suite,
            model="offline-deterministic",
            component_fingerprint=self._component_fingerprint,
            live=False,
            case_ids=tuple(item.case_id for item in cases),
            observation_ids=tuple(observation_ids),
            status="completed",
            started_at=started,
            completed_at=_now(),
        )
        self._repository.save_run(run)
        return run

    def propose(self, model: ModelClient, component_id: str = "") -> HarnessCandidate:
        """Use one explicit model call to create a non-executable structured proposal."""
        if not self._candidates_enabled:
            raise ToolExecutionError("Candidate proposals are disabled")
        selected = self._select_components(component_id)
        history = self.history(50)
        failures = [failure for item in history for failure in item.failures][:20]
        prompt = json.dumps(
            {
                "components": [asdict(item) for item in selected],
                "aggregate": self.status(),
                "failure_examples": failures,
            },
            ensure_ascii=False,
        )[: self._max_trace_chars]
        completion = model.complete(
            [
                Message(
                    "system",
                    content=(
                        "Propose one measurable harness improvement. Return only JSON with keys "
                        "proposal, predicted_changes, evidence_ids, risks, rollback_instructions, "
                        "required_suite. Do not provide code or hidden reasoning."
                    ),
                ),
                Message("user", content=prompt),
            ],
            [],
        )
        message = completion if isinstance(completion, Message) else completion.message
        try:
            value = json.loads(message.content or "")
            if not isinstance(value, dict):
                raise ValueError
            proposal = self._sanitize(str(value["proposal"]))[:4_000]
            predicted = _strings(value["predicted_changes"], 10)
            evidence_ids = _strings(value["evidence_ids"], 20)
            risks = _strings(value["risks"], 10)
            rollback = self._sanitize(str(value["rollback_instructions"]))[:1_000]
            suite = self._sanitize(str(value["required_suite"]))[:100]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolExecutionError("Candidate proposal was not valid structured JSON") from exc
        now = _now()
        candidate = HarnessCandidate(
            candidate_id=new_session_id(),
            component_ids=tuple(item.component_id for item in selected),
            proposal=proposal,
            predicted_changes=predicted,
            evidence_ids=evidence_ids,
            risks=risks,
            rollback_instructions=rollback,
            required_suite=suite,
            created_at=now,
            updated_at=now,
        )
        self._repository.save_candidate(candidate)
        return candidate

    def candidate(self, candidate_id: str) -> HarnessCandidate:
        """Return one candidate or raise a user-facing error."""
        value = self._repository.get_candidate(candidate_id)
        if value is None:
            raise SessionError("Candidate proposal was not found")
        return value

    def candidates(self, limit: int = 20) -> tuple[HarnessCandidate, ...]:
        """Return recent candidate proposals newest first."""
        return self._repository.list_candidates(limit)

    def decide_candidate(
        self, candidate_id: str, approved: bool, feedback: str = ""
    ) -> HarnessCandidate:
        """Record approval or rejection without applying source changes."""
        current = self.candidate(candidate_id)
        updated = replace(
            current,
            status="approved" if approved else "rejected",
            feedback=self._sanitize(" ".join(feedback.split()))[:500],
            updated_at=_now(),
        )
        self._repository.save_candidate(updated)
        return updated

    def _select_components(self, component_id: str) -> tuple[ComponentSnapshot, ...]:
        if component_id:
            try:
                return (self._components[component_id],)
            except KeyError as exc:
                raise ToolExecutionError(f"Unknown candidate component: {component_id}") from exc
        return tuple(self._components.values())


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _strings(value: object, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError
    return tuple(str(item)[:500] for item in value[:limit])


def _metadata_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _rate_delta(
    pairs: list[tuple[EvaluationObservation, EvaluationObservation]],
    getter: Callable[[EvaluationObservation], bool],
) -> float:
    if not pairs:
        return 0.0
    left = sum(getter(item[0]) for item in pairs) * 100 / len(pairs)
    right = sum(getter(item[1]) for item in pairs) * 100 / len(pairs)
    return round(right - left, 2)


def _median_delta(
    pairs: list[tuple[EvaluationObservation, EvaluationObservation]],
    getter: Callable[[EvaluationObservation], int],
) -> float:
    if not pairs:
        return 0.0
    left = statistics.median(getter(item[0]) for item in pairs)
    right = statistics.median(getter(item[1]) for item in pairs)
    if left == 0:
        return 0.0 if right == 0 else 100.0
    return round((right - left) * 100 / left, 2)
