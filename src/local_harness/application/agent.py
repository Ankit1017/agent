"""Bounded conversational agent use case."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from time import perf_counter

from local_harness.application.answer_quality import (
    AnswerQualityPolicy,
    normalize_assistant_markdown,
)
from local_harness.application.context import ContextBuilder
from local_harness.application.evaluation import EvaluationService
from local_harness.application.evidence import (
    append_verification,
    build_completion_evidence,
    enforce_evidence_consistency,
)
from local_harness.application.ports import (
    ModelClient,
    ProgressSink,
    ProjectIndexRepository,
    SessionRepository,
)
from local_harness.application.progress import (
    add_step_summary_schema,
    extract_final_summary,
    normalize_summary,
)
from local_harness.application.tool_registry import ToolRegistry
from local_harness.application.tool_routing import RequestToolRouter, ToolDescriptor
from local_harness.application.workflows import (
    WorkflowCatalog,
    WorkflowCoordinator,
    WorkflowMode,
    WorkflowSelector,
)
from local_harness.domain.errors import HarnessError, TaskCancelledError, ToolExecutionError
from local_harness.domain.limits import validate_max_turns
from local_harness.domain.models import (
    Message,
    ModelCompletion,
    ProgressEvent,
    ProgressEventKind,
    ProgressStatus,
    Session,
    SessionSummary,
    TokenUsage,
    ToolCall,
    ToolDefinition,
    ToolResult,
    WorkflowDefinition,
    WorkflowRun,
)
from local_harness.domain.project_memory import (
    ProjectIndexStatus,
    ProjectMemoryQuery,
    RetrievedProjectContext,
)


class AgentService:
    """Coordinate model turns, tool execution, and durable conversation state."""

    def __init__(
        self,
        *,
        model_client: ModelClient,
        registry: ToolRegistry,
        sessions: SessionRepository,
        session: Session,
        system_prompt: str,
        max_turns: int,
        max_turns_source: str = "startup",
        progress_sink: ProgressSink | None = None,
        context_builder: ContextBuilder | None = None,
        clock: Callable[[], float] = perf_counter,
        sanitizer: Callable[[str], tuple[str, bool]] | None = None,
        token_budget: int = 0,
        token_warning_percent: int = 80,
        answer_quality: AnswerQualityPolicy | None = None,
        tool_router: RequestToolRouter | None = None,
        project_memory: ProjectIndexRepository | None = None,
        workflow_catalog: WorkflowCatalog | None = None,
        workflow_selector: WorkflowSelector | None = None,
        workflow_mode: WorkflowMode = "off",
        workflow_stage_max_attempts: int = 2,
        evaluation_service: EvaluationService | None = None,
        context_max_chars: int = 60_000,
        command_timeout_seconds: int = 120,
        cancellation_requested: Callable[[], bool] | None = None,
        max_answer_chars: int = 0,
    ) -> None:
        """Create a bounded agent for one session."""
        self._model_client = model_client
        self._registry = registry
        self._sessions = sessions
        self.session = session
        self._system_prompt = system_prompt
        self._baseline_max_turns = validate_max_turns(max_turns)
        self._baseline_max_turns_source = max_turns_source
        self._max_turns = self._baseline_max_turns
        self._max_turns_source = max_turns_source
        self._progress_sink = progress_sink
        self._context_builder = context_builder or ContextBuilder(60_000)
        self._clock = clock
        self._sanitizer = sanitizer or (lambda value: (value, False))
        self._baseline_token_budget = token_budget
        self._token_warning_percent = token_warning_percent
        self._answer_quality = answer_quality or AnswerQualityPolicy()
        self._tool_router = tool_router
        self._project_memory = project_memory
        self._workflow_catalog = workflow_catalog or WorkflowCatalog()
        self._workflow_selector = workflow_selector or WorkflowSelector(self._workflow_catalog)
        self._workflow_mode = workflow_mode
        self._workflow = WorkflowCoordinator(
            session, self._workflow_catalog, workflow_stage_max_attempts
        )
        self._evaluation = evaluation_service
        self._context_max_chars = context_max_chars
        self._command_timeout_seconds = command_timeout_seconds
        self._cancellation_requested = cancellation_requested or (lambda: False)
        self._max_answer_chars = max_answer_chars

    @property
    def max_turns(self) -> int:
        """Return the effective LLM-call limit for each user request."""
        return self._max_turns

    @property
    def max_turns_source(self) -> str:
        """Return the source of the effective LLM-call limit."""
        return self._max_turns_source

    @property
    def next_request_number(self) -> int:
        """Return the stable number that will be assigned to the next request."""
        tagged = [
            value
            for value in (
                *(message.request_number for message in self.session.messages),
                *(event.request_number for event in self.session.events),
            )
            if value is not None
        ]
        legacy_user_count = sum(message.role == "user" for message in self.session.messages)
        return max([legacy_user_count, *tagged], default=0) + 1

    @property
    def token_usage(self) -> int:
        """Return total recorded model tokens for the current session."""
        return sum(event.input_tokens + event.output_tokens for event in self.session.events)

    @property
    def token_budget(self) -> int:
        """Return the advisory session token budget, or zero when disabled."""
        override = self.session.token_budget_override
        return override if override is not None else self._baseline_token_budget

    def sanitize_input(self, value: str) -> tuple[str, bool]:
        """Return the safe prompt that will be displayed, stored, and sent."""
        return self._sanitizer(value)

    def configure_token_budget(self, value: int | None) -> int:
        """Persist an advisory token budget override or reset it to startup configuration."""
        if value is not None and (isinstance(value, bool) or value <= 0):
            raise ValueError("Token budget must be a positive integer")
        self.session.token_budget_override = value
        self._sessions.save(self.session)
        return self.token_budget

    def configure_max_turns(self, value: int | None) -> int:
        """Persist a session override or reset to the startup baseline."""
        if value is None:
            self._max_turns = self._baseline_max_turns
            self._max_turns_source = self._baseline_max_turns_source
            self.session.max_turns_override = None
        else:
            self._max_turns = validate_max_turns(value)
            self._max_turns_source = "session"
            self.session.max_turns_override = self._max_turns
        self._sessions.save(self.session)
        return self._max_turns

    def submit(self, user_input: str, workflow_id: str | None = None) -> str:
        """Process one user request until a final answer or the turn limit."""
        safe_input, was_redacted = self.sanitize_input(user_input)
        if not safe_input.strip():
            return "Please enter a task or question."
        request_number = self.next_request_number
        if was_redacted:
            self._record_event(
                call_number=max((event.call_number for event in self.session.events), default=0),
                kind="security_notice",
                summary="Credential-like text redacted from prompt",
                target="prompt",
                status="success",
                request_number=request_number,
            )
        self._append(
            Message(role="user", content=safe_input.strip(), request_number=request_number)
        )
        pending = self.session.pending_workflow_override
        workflow_run: WorkflowRun | None = None
        if workflow_id is not None:
            workflow_selection = self._workflow_selector.select(safe_input, workflow_id)
        elif pending is not None:
            workflow_selection = self._workflow_selector.select(
                safe_input, pending, source="pending"
            )
            self.session.pending_workflow_override = None
        elif self._workflow_mode == "auto":
            workflow_selection = self._workflow_selector.select(safe_input)
        else:
            workflow_selection = None
        if workflow_selection is not None:
            workflow_run = self._workflow.start(request_number, workflow_selection)
            self._record_event(
                call_number=max((event.call_number for event in self.session.events), default=0),
                kind="workflow_selected",
                summary=f"Selected workflow: {workflow_run.workflow_id}",
                target=workflow_run.workflow_id,
                status="success",
                request_number=request_number,
                metadata={
                    "confidence": workflow_run.confidence,
                    "selection_source": workflow_run.selection_source,
                    "matched_signals": list(workflow_run.matched_signals),
                },
            )
        else:
            self._workflow.clear()
        if self._tool_router is not None:
            tool_selection = self._tool_router.start(safe_input)
            if workflow_run is not None and workflow_run.workflow_id != "general_assistance":
                self._tool_router.set_workflow_stage(
                    self._workflow.allowed_tools(), self._workflow.all_tools()
                )
            savings = max(
                0,
                tool_selection.catalog_schema_chars - tool_selection.selected_schema_chars,
            )
            self._record_event(
                call_number=max((event.call_number for event in self.session.events), default=0),
                kind="tool_profile",
                summary=(
                    f"Selected {tool_selection.profile} tools; saved {savings} schema characters"
                ),
                target=", ".join(tool_selection.names),
                status="success",
                request_number=request_number,
                metadata={
                    "profile": tool_selection.profile,
                    "activated_tools": list(tool_selection.names),
                    "catalog_schema_chars": tool_selection.catalog_schema_chars,
                    "selected_schema_chars": tool_selection.selected_schema_chars,
                    "schema_chars_saved": savings,
                },
            )
        project_context = self._retrieve_project_context(safe_input, request_number)
        recovery_instruction: str | None = None
        quality_fallback: tuple[str, str] | None = None
        quality_retry_used = False
        failed_calls: dict[str, int] = {}
        definition = self._workflow.definition
        request_max_turns = min(
            self._max_turns,
            definition.suggested_call_budget if definition is not None else self._max_turns,
        )
        if self._evaluation is not None:
            self._evaluation.begin_request(
                self.session,
                request_number,
                safe_input,
                definition,
                max_llm_calls=request_max_turns,
                max_tokens=self.session.token_budget_override or self._baseline_token_budget,
                max_context_chars=self._context_max_chars,
                max_runtime_ms=request_max_turns * self._command_timeout_seconds * 1_000,
            )
        for turn_index in range(request_max_turns):
            self._check_cancelled()
            call_number = self._next_call_number()
            self._record_event(
                call_number=call_number,
                kind="model_start",
                summary=f"Waiting for {self.session.model}",
                target=self.session.model,
                status="started",
                request_number=request_number,
            )
            available = (
                self._tool_router.definitions()
                if self._tool_router is not None
                else [tool.definition for tool in self._registry.tools]
            )
            definitions = [add_step_summary_schema(item) for item in available]
            workflow_instruction = self._workflow.instruction()
            provider_context = project_context
            if workflow_instruction:
                provider_context = f"{project_context}\n\n{workflow_instruction}".strip()
            messages = self._context_builder.build(
                self._system_prompt,
                self.session.messages,
                definitions,
                request_number,
                provider_context,
            )
            is_recovery_call = recovery_instruction is not None
            if recovery_instruction is not None:
                messages.append(Message(role="user", content=recovery_instruction))
                recovery_instruction = None
            started_at = self._clock()
            try:
                completion = self._model_client.complete(
                    messages,
                    definitions,
                )
            except HarnessError:
                self._record_event(
                    call_number=call_number,
                    kind="model_error",
                    summary="Model call failed",
                    target=self.session.model,
                    status="error",
                    duration_ms=self._elapsed_ms(started_at),
                    request_number=request_number,
                )
                if is_recovery_call and quality_fallback is not None:
                    return self._finish_quality_fallback(
                        quality_fallback,
                        call_number=call_number,
                        request_number=request_number,
                    )
                raise
            if isinstance(completion, Message):
                assistant = completion
                usage = _estimate_usage(messages, definitions, assistant)
            else:
                assistant = completion.message
                usage = completion.usage or _estimate_usage(messages, definitions, assistant)
            if assistant.role != "assistant":
                self._record_event(
                    call_number=call_number,
                    kind="model_error",
                    summary="Model returned an invalid response",
                    target=self.session.model,
                    status="error",
                    duration_ms=self._elapsed_ms(started_at),
                    request_number=request_number,
                )
                raise ToolExecutionError("Model adapter returned a non-assistant message")
            if not assistant.tool_calls:
                summary, content = extract_final_summary(assistant.content)
                if not content.strip():
                    self._record_event(
                        call_number=call_number,
                        kind="model_error",
                        summary="Model returned an empty response; retrying",
                        target="final",
                        status="error",
                        duration_ms=self._elapsed_ms(started_at),
                        request_number=request_number,
                        usage=usage,
                    )
                    if is_recovery_call and quality_fallback is not None:
                        return self._finish_quality_fallback(
                            quality_fallback,
                            call_number=call_number,
                            request_number=request_number,
                        )
                    recovery_instruction = (
                        "Your previous response was empty. Continue the original request now: "
                        "return a substantive final answer with the required step_summary, or call "
                        "one valid tool from the supplied tool list."
                    )
                    continue
                normalized = normalize_assistant_markdown(content)
                if self._max_answer_chars:
                    normalized = normalize_assistant_markdown(
                        normalized[: self._max_answer_chars].rstrip()
                    )
                evidence = build_completion_evidence(
                    self.session.messages,
                    self.session.events,
                    self.session.plans,
                    request_number,
                    self.session.workflows,
                )
                workflow_issues = self._workflow.completion_issues(
                    changed=bool(evidence.changed_files),
                    successful_check=any(
                        value.casefold().endswith(("completed", "passed", "success"))
                        for value in evidence.checks
                    ),
                    sources=bool(evidence.sources),
                    measurements=sum(
                        event.target == "run_powershell" and event.status == "success"
                        for event in self.session.events
                        if event.request_number == request_number
                    ),
                )
                evidence = replace(evidence, unmet_requirements=workflow_issues)
                self.session.evidence = [
                    item for item in self.session.evidence if item.request_number != request_number
                ]
                self.session.evidence.append(evidence)
                normalized = enforce_evidence_consistency(normalized, evidence)
                normalized = append_verification(normalized, evidence)
                if self._max_answer_chars:
                    normalized = normalize_assistant_markdown(
                        normalized[: self._max_answer_chars].rstrip()
                    )
                assessment = self._answer_quality.assess(
                    content, self.session.messages, request_number, evidence
                )
                if (
                    workflow_issues
                    and self._workflow.run is not None
                    and self._workflow.run.status == "active"
                    and not quality_retry_used
                    and turn_index + 1 < request_max_turns
                ):
                    quality_fallback = (summary, normalized)
                    quality_retry_used = True
                    self._record_event(
                        call_number=call_number,
                        kind="workflow_stage",
                        summary="Workflow requirements remain incomplete",
                        target=self._workflow.run.current_stage_id,
                        status="warning",
                        duration_ms=self._elapsed_ms(started_at),
                        request_number=request_number,
                        usage=usage,
                        metadata={"requirements": list(workflow_issues)},
                    )
                    recovery_instruction = (
                        "The selected workflow is incomplete. Continue with the current "
                        "observable stage using an allowed tool. Requirements: "
                        f"{'; '.join(workflow_issues)}"
                    )
                    continue
                has_retry_slot = turn_index + 1 < request_max_turns
                if not assessment.acceptable and not quality_retry_used and has_retry_slot:
                    quality_fallback = (summary, normalized)
                    quality_retry_used = True
                    self._record_event(
                        call_number=call_number,
                        kind="model_error",
                        summary="Answer quality incomplete; retrying",
                        target="final",
                        status="warning",
                        duration_ms=self._elapsed_ms(started_at),
                        request_number=request_number,
                        usage=usage,
                    )
                    recovery_instruction = self._answer_quality.correction_instruction(assessment)
                    continue
                if not assessment.acceptable:
                    selected = quality_fallback or (summary, normalized)
                    self._append(
                        Message(
                            role="assistant",
                            content=selected[1],
                            request_number=request_number,
                        )
                    )
                    self._record_event(
                        call_number=call_number,
                        kind="model_complete",
                        summary="Answer completed with formatting warning",
                        target="final",
                        status="warning",
                        duration_ms=self._elapsed_ms(started_at),
                        request_number=request_number,
                        usage=usage,
                    )
                    self._refresh_summary()
                    self._refresh_dirty_memory(request_number)
                    self._capture_evaluation(request_number)
                    return selected[1]
                clean_assistant = Message(
                    role="assistant", content=normalized, request_number=request_number
                )
                self._append(clean_assistant)
                self._record_event(
                    call_number=call_number,
                    kind="model_complete",
                    summary=summary,
                    target="final",
                    status="success",
                    duration_ms=self._elapsed_ms(started_at),
                    request_number=request_number,
                    usage=usage,
                )
                self._refresh_summary()
                self._refresh_dirty_memory(request_number)
                self._capture_evaluation(request_number)
                return normalized
            assistant = self._repair_placeholder_calls(assistant)
            self._append(replace(assistant, request_number=request_number))
            summaries = [self._call_summary(call) for call in assistant.tool_calls]
            targets = ", ".join(call.name for call in assistant.tool_calls)
            self._record_event(
                call_number=call_number,
                kind="model_complete",
                summary=normalize_summary("; ".join(summaries), "Requested tools"),
                target=targets,
                status="success",
                duration_ms=self._elapsed_ms(started_at),
                request_number=request_number,
                usage=usage,
            )
            for call in assistant.tool_calls:
                self._check_cancelled()
                tool_started_at = self._clock()
                signature = _call_signature(call)
                if failed_calls.get(signature, 0) >= 2:
                    result = ToolResult(
                        "Repeated failing tool call blocked. Change the arguments or use "
                        "discover_tools for an alternative.",
                        True,
                    )
                else:
                    result = self._execute_call(call)
                if result.is_error:
                    failed_calls[signature] = failed_calls.get(signature, 0) + 1
                elif call.name == "apply_patch":
                    self._mark_patch_paths(result.content)
                self._append(
                    Message(
                        role="tool",
                        content=result.content,
                        tool_call_id=call.id,
                        name=call.name,
                        request_number=request_number,
                    )
                )
                self._record_event(
                    call_number=call_number,
                    kind=(
                        "tool_error"
                        if result.is_error
                        else "plan_update"
                        if call.name == "task_plan"
                        else "tool_complete"
                    ),
                    summary=self._call_summary(call),
                    target=call.name,
                    status="error" if result.is_error else "success",
                    duration_ms=self._elapsed_ms(tool_started_at),
                    request_number=request_number,
                    metadata=(
                        {"activated_tools": list(self._tool_router.active_names)}
                        if call.name == "discover_tools" and self._tool_router is not None
                        else None
                    ),
                )
                previous = self._workflow.run
                updated = self._workflow.after_tool(
                    call.name,
                    is_error=result.is_error,
                    summary=self._call_summary(call),
                )
                if updated is not None and updated != previous:
                    kind: ProgressEventKind = (
                        "workflow_blocked"
                        if updated.status == "blocked"
                        else "workflow_complete"
                        if updated.status == "completed"
                        else "workflow_stage"
                    )
                    self._record_event(
                        call_number=call_number,
                        kind=kind,
                        summary=(
                            f"Workflow {updated.status}: {updated.workflow_id}"
                            if updated.status != "active"
                            else f"Advanced workflow to {updated.current_stage_id}"
                        ),
                        target=updated.current_stage_id or updated.workflow_id,
                        status=("error" if updated.status == "blocked" else "success"),
                        request_number=request_number,
                        metadata={"workflow_id": updated.workflow_id},
                    )
                    if self._tool_router is not None and updated.status == "active":
                        self._tool_router.set_workflow_stage(
                            self._workflow.allowed_tools(), self._workflow.all_tools()
                        )
        limit_message = (
            f"Stopped after {request_max_turns} LLM calls for this request. "
            "Refine the request or continue in a new message."
        )
        self._append(
            Message(role="assistant", content=limit_message, request_number=request_number)
        )
        self._refresh_summary()
        self._refresh_dirty_memory(request_number)
        self._capture_evaluation(request_number)
        return limit_message

    def _finish_quality_fallback(
        self,
        fallback: tuple[str, str],
        *,
        call_number: int,
        request_number: int,
    ) -> str:
        """Persist a substantive first answer when its correction call fails."""
        _, content = fallback
        self._append(Message(role="assistant", content=content, request_number=request_number))
        self._record_event(
            call_number=call_number,
            kind="model_complete",
            summary="Answer completed with formatting warning",
            target="final",
            status="warning",
            request_number=request_number,
        )
        self._refresh_summary()
        self._refresh_dirty_memory(request_number)
        self._capture_evaluation(request_number)
        return content

    def _capture_evaluation(self, request_number: int) -> None:
        """Persist deterministic evaluation evidence after the request boundary."""
        if self._evaluation is not None:
            self._evaluation.complete_request(self.session, request_number)

    def project_index_status(self) -> ProjectIndexStatus | None:
        """Return project-memory status, or ``None`` when indexing is disabled."""
        return self._project_memory.status() if self._project_memory is not None else None

    def refresh_project_index(self, *, rebuild: bool = False) -> ProjectIndexStatus:
        """Refresh project memory explicitly for a shared interface command."""
        if self._project_memory is None:
            raise ToolExecutionError("Project memory is disabled")
        return self._project_memory.refresh(rebuild=rebuild)

    def query_project_memory(self, query: str) -> RetrievedProjectContext:
        """Inspect retrieval results without making an LLM call."""
        if self._project_memory is None:
            raise ToolExecutionError("Project memory is disabled")
        return self._project_memory.retrieve(ProjectMemoryQuery(query))

    def _retrieve_project_context(self, prompt: str, request_number: int) -> str:
        if self._project_memory is None or (
            self._tool_router is not None and self._tool_router.profile != "coding"
        ):
            return ""
        started_at = self._clock()
        self._record_event(
            call_number=max((event.call_number for event in self.session.events), default=0),
            kind="index_update",
            summary="Refreshing project memory",
            target="project-memory",
            status="started",
            request_number=request_number,
        )
        try:
            result = self._project_memory.retrieve_for_request(prompt)
        except HarnessError as exc:
            self._record_event(
                call_number=max((event.call_number for event in self.session.events), default=0),
                kind="memory_retrieval",
                summary="Project memory unavailable; continuing without indexed context",
                target="project-memory",
                status="warning",
                duration_ms=self._elapsed_ms(started_at),
                request_number=request_number,
                metadata={"fallback": True, "warning": str(exc)[:500]},
            )
            return ""
        self._record_event(
            call_number=max((event.call_number for event in self.session.events), default=0),
            kind="memory_retrieval",
            summary=f"Retrieved {len(result.hits)} relevant project source(s)",
            target="project-memory",
            status="warning" if result.warning else "success",
            duration_ms=self._elapsed_ms(started_at),
            request_number=request_number,
            metadata={
                "generation": result.generation,
                "retrieval_mode": result.retrieval_mode,
                "selected_paths": sorted({item.path for item in result.hits}),
                "injected_chars": result.injected_chars,
                "candidate_chars_avoided": max(0, result.candidate_chars - result.injected_chars),
                "fallback": result.retrieval_mode == "lexical",
            },
        )
        return result.rendered

    def _mark_patch_paths(self, content: str) -> None:
        if self._project_memory is None:
            return
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            return
        paths = [
            str(item["path"])
            for item in payload["items"]
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ]
        if paths:
            self._project_memory.mark_dirty(paths)

    def _refresh_dirty_memory(self, request_number: int) -> None:
        if self._project_memory is None or not self._project_memory.status().stale:
            return
        started_at = self._clock()
        try:
            status = self._project_memory.refresh()
        except HarnessError as exc:
            self._record_event(
                call_number=max((event.call_number for event in self.session.events), default=0),
                kind="index_update",
                summary="Project-memory refresh deferred",
                target="project-memory",
                status="warning",
                duration_ms=self._elapsed_ms(started_at),
                request_number=request_number,
                metadata={"warning": str(exc)[:500]},
            )
            return
        self._record_event(
            call_number=max((event.call_number for event in self.session.events), default=0),
            kind="index_update",
            summary=f"Project index refreshed to generation {status.generation}",
            target="project-memory",
            status="success",
            duration_ms=self._elapsed_ms(started_at),
            request_number=request_number,
            metadata={"generation": status.generation, "files": status.files},
        )

    def summarize_with_model(self) -> str:
        """Generate and persist one explicit bounded session summary model call."""
        exchanges = [
            message
            for message in self.session.messages
            if message.role in {"user", "assistant"} and message.content
        ][-40:]
        transcript = "\n".join(f"{item.role}: {item.content}" for item in exchanges)
        transcript = transcript[-20_000:]
        messages = [
            Message(
                role="system",
                content=(
                    "Summarize observable outcomes only in at most 150 words. "
                    "Do not invent facts or reveal hidden reasoning."
                ),
            ),
            Message(role="user", content=transcript or "This session is empty."),
        ]
        call_number = self._next_call_number()
        self._record_event(
            call_number=call_number,
            kind="model_start",
            summary=f"Waiting for {self.session.model}",
            target="session-summary",
            status="started",
        )
        started_at = self._clock()
        try:
            raw_completion = self._model_client.complete(messages, [])
            completion = (
                ModelCompletion(raw_completion)
                if isinstance(raw_completion, Message)
                else raw_completion
            )
            content = completion.message.content or ""
            _, cleaned = extract_final_summary(content)
            cleaned = " ".join(cleaned.split())[:1_000]
            if not cleaned:
                raise ToolExecutionError("Model returned an empty session summary")
            usage = completion.usage or _estimate_usage(messages, [], completion.message)
            self.session.summary = SessionSummary(cleaned, "llm")
            self._record_event(
                call_number=call_number,
                kind="summary_complete",
                summary="Generated session summary",
                target="session-summary",
                status="success",
                duration_ms=self._elapsed_ms(started_at),
                usage=usage,
            )
            return cleaned
        except HarnessError:
            self._record_event(
                call_number=call_number,
                kind="summary_error",
                summary="Session summary failed",
                target="session-summary",
                status="error",
                duration_ms=self._elapsed_ms(started_at),
            )
            raise

    def _execute_call(self, call: ToolCall) -> ToolResult:
        try:
            decoded = json.loads(call.arguments)
            if not isinstance(decoded, dict):
                raise ValueError("arguments must be a JSON object")
            decoded.pop("step_summary", None)
            arguments: Mapping[str, object] = decoded
            name = _TOOL_ALIASES.get(call.name, call.name)
            workflow_error = self._workflow.before_tool(name)
            if workflow_error:
                return ToolResult(workflow_error, True)
            if self._tool_router is not None and not self._tool_router.is_active(name):
                return ToolResult(
                    f"Tool {name} is inactive for this request. Call discover_tools with the "
                    "needed capability first.",
                    True,
                )
            return self._registry.get(name).execute(arguments)
        except (json.JSONDecodeError, ValueError, HarnessError) as exc:
            return ToolResult(f"Tool call rejected: {exc}", True)

    def _repair_placeholder_calls(self, assistant: Message) -> Message:
        """Repair an obvious placeholder name when arguments match exactly one tool schema."""
        repaired: list[ToolCall] = []
        for call in assistant.tool_calls:
            try:
                decoded = json.loads(call.arguments)
            except json.JSONDecodeError:
                repaired.append(call)
                continue
            if not isinstance(decoded, dict):
                repaired.append(call)
                continue
            resolved = self._registry.resolve_placeholder(call.name, decoded)
            repaired.append(ToolCall(call.id, resolved, call.arguments))
        return replace(assistant, tool_calls=tuple(repaired))

    def tool_catalog(self, query: str = "") -> tuple[ToolDescriptor, ...]:
        """Return compact catalog entries for interface inspection."""
        if self._tool_router is None:
            return ()
        return self._tool_router.catalog(query)

    def workflow_catalog(self, query: str = "") -> tuple[WorkflowDefinition, ...]:
        """Return built-in workflow definitions for shared interfaces."""
        return self._workflow_catalog.list(query)

    def configure_workflow(self, workflow_id: str | None) -> str | None:
        """Persist or clear a one-shot workflow override for the next request."""
        if workflow_id is not None:
            self._workflow_catalog.get(workflow_id)
        self.session.pending_workflow_override = workflow_id
        self._sessions.save(self.session)
        return workflow_id

    def workflow_status(self) -> WorkflowRun | None:
        """Return the latest persisted workflow run."""
        return self.session.workflows[-1] if self.session.workflows else None

    def _append(self, message: Message) -> None:
        self.session.messages.append(message)
        self._sessions.save(self.session)

    def _record_event(
        self,
        *,
        call_number: int,
        kind: ProgressEventKind,
        summary: str,
        target: str,
        status: ProgressStatus,
        duration_ms: int = 0,
        request_number: int | None = None,
        usage: TokenUsage | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        event = ProgressEvent(
            sequence=max((item.sequence for item in self.session.events), default=0) + 1,
            call_number=call_number,
            kind=kind,
            summary=normalize_summary(summary, "Progress update"),
            target=target,
            status=status,
            duration_ms=duration_ms,
            request_number=request_number,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            usage_source=usage.source if usage else "unknown",
            metadata=metadata or {},
        )
        self.session.events.append(event)
        self._sessions.save(self.session)
        if self._progress_sink is not None:
            self._progress_sink.publish(event)
        if usage is not None:
            self._maybe_warn_quota(call_number)

    def _next_call_number(self) -> int:
        return max((event.call_number for event in self.session.events), default=0) + 1

    def _check_cancelled(self) -> None:
        if self._cancellation_requested():
            raise TaskCancelledError("Task was cancelled")

    def _call_summary(self, call: ToolCall) -> str:
        try:
            arguments = json.loads(call.arguments)
        except json.JSONDecodeError:
            return f"Requested {call.name}"
        if not isinstance(arguments, dict):
            return f"Requested {call.name}"
        return normalize_summary(arguments.get("step_summary"), f"Requested {call.name}")

    def _elapsed_ms(self, started_at: float) -> int:
        return max(0, round((self._clock() - started_at) * 1000))

    def _refresh_summary(self) -> None:
        completed = [
            event
            for event in self.session.events
            if event.kind == "model_complete" and event.target == "final"
        ]
        failures = sum(event.status == "error" for event in self.session.events)
        recent = "; ".join(event.summary for event in completed[-3:]) or "No completed outcome"
        text = f"{self.next_request_number - 1} request(s), {failures} error(s). Recent: {recent}"
        self.session.summary = SessionSummary(text[:1_000], "deterministic")
        self._sessions.save(self.session)

    def _maybe_warn_quota(self, call_number: int) -> None:
        budget = self.token_budget
        if budget <= 0:
            return
        percent = (self.token_usage * 100) // budget
        for threshold in (self._token_warning_percent, 100):
            if percent < threshold:
                continue
            marker = f"{threshold}%"
            if any(
                event.kind == "quota_warning" and event.target == marker
                for event in self.session.events
            ):
                continue
            self._record_event(
                call_number=call_number,
                kind="quota_warning",
                summary=f"Advisory token budget reached {percent}%",
                target=marker,
                status="warning",
            )


def _estimate_usage(
    messages: Sequence[Message], definitions: Sequence[ToolDefinition], assistant: Message
) -> TokenUsage:
    input_chars = sum(len(message.content or "") for message in messages) + len(
        json.dumps(definitions, default=str)
    )
    output_chars = len(assistant.content or "") + sum(
        len(call.arguments) + len(call.name) for call in assistant.tool_calls
    )
    return TokenUsage(math.ceil(input_chars / 4), math.ceil(output_chars / 4), "estimated")


_TOOL_ALIASES = {
    "search_code": "find_code",
    "read_multiple_files": "read_files",
    "project_overview": "inspect_project",
}


def _call_signature(call: ToolCall) -> str:
    """Return a stable name-and-arguments signature for loop prevention."""
    name = _TOOL_ALIASES.get(call.name, call.name).strip().casefold()
    try:
        arguments = json.loads(call.arguments)
        if isinstance(arguments, dict):
            arguments.pop("step_summary", None)
        normalized = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    except json.JSONDecodeError:
        normalized = " ".join(call.arguments.split())
    return f"{name}:{normalized}"
