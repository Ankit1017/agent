"""Domain models exchanged by the harness layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

MessageRole = Literal["system", "user", "assistant", "tool"]
ProgressEventKind = Literal[
    "model_start",
    "model_complete",
    "model_error",
    "tool_complete",
    "tool_error",
    "summary_complete",
    "summary_error",
    "quota_warning",
    "security_notice",
    "tool_profile",
    "plan_update",
    "index_update",
    "memory_retrieval",
    "workflow_selected",
    "workflow_stage",
    "workflow_complete",
    "workflow_blocked",
]
ProgressStatus = Literal["started", "success", "warning", "error"]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model request to invoke one named tool."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class Message:
    """One provider-neutral conversation message."""

    role: MessageRole
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None
    request_number: int | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A serializable result returned by a tool."""

    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Provider-reported or deterministically estimated model token usage."""

    input_tokens: int
    output_tokens: int
    source: Literal["provider", "estimated", "unknown"] = "unknown"

    @property
    def total_tokens(self) -> int:
        """Return the combined input and output token count."""
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class ModelCompletion:
    """One model message plus optional provider usage accounting."""

    message: Message
    usage: TokenUsage | None = None

    @property
    def role(self) -> MessageRole:
        """Expose the message role for compatibility with message-oriented callers."""
        return self.message.role

    @property
    def content(self) -> str | None:
        """Expose message content for compatibility with existing adapters."""
        return self.message.content

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        """Expose tool calls for compatibility with existing callers."""
        return self.message.tool_calls


@dataclass(frozen=True, slots=True)
class AnswerQualityIssue:
    """One deterministic issue found in a proposed final answer."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class AnswerQualityAssessment:
    """Result of validating answer structure and web-source provenance."""

    issues: tuple[AnswerQualityIssue, ...] = ()
    source_urls: tuple[str, ...] = ()

    @property
    def acceptable(self) -> bool:
        """Return whether the answer can be presented without a warning."""
        return not self.issues


@dataclass(frozen=True, slots=True)
class TaskStep:
    """One observable step in a persisted request plan."""

    step_id: int
    description: str
    status: Literal["pending", "in_progress", "completed", "blocked"] = "pending"
    result: str = ""
    requires_verification: bool = False


@dataclass(frozen=True, slots=True)
class TaskPlan:
    """A concise persisted plan associated with one user request."""

    request_number: int
    goal: str
    steps: tuple[TaskStep, ...]
    status: Literal["active", "completed", "blocked"] = "active"
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True, slots=True)
class CompletionEvidence:
    """Deterministic evidence collected while completing one request."""

    request_number: int
    changed_files: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    workflow_id: str = ""
    completed_stages: tuple[str, ...] = ()
    blocked_stages: tuple[str, ...] = ()
    unmet_requirements: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowCompletionRule:
    """Deterministic evidence required before a workflow can complete."""

    require_changed_files: bool = False
    require_successful_check: bool = False
    require_sources: bool = False
    require_measurements: bool = False


@dataclass(frozen=True, slots=True)
class WorkflowStageDefinition:
    """One observable stage in a built-in workflow definition."""

    stage_id: str
    description: str
    tools: tuple[str, ...]
    required: bool = True


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    """Versioned immutable situation-based workflow definition."""

    workflow_id: str
    title: str
    description: str
    version: int
    triggers: tuple[str, ...]
    negative_triggers: tuple[str, ...]
    priority: int
    stages: tuple[WorkflowStageDefinition, ...]
    completion: WorkflowCompletionRule = field(default_factory=WorkflowCompletionRule)
    suggested_call_budget: int = 12


@dataclass(frozen=True, slots=True)
class WorkflowSelection:
    """Deterministic workflow selection for one sanitized request."""

    workflow_id: str
    confidence: float
    source: Literal["automatic", "explicit", "pending", "fallback"]
    matched_signals: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowStageRun:
    """Persisted execution state for one observable workflow stage."""

    stage_id: str
    description: str
    status: Literal["pending", "in_progress", "completed", "skipped", "blocked"] = "pending"
    attempts: int = 0
    result: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    """Persisted request-level execution of a built-in workflow."""

    request_number: int
    workflow_id: str
    workflow_version: int
    selection_source: Literal["automatic", "explicit", "pending", "fallback"]
    confidence: float
    matched_signals: tuple[str, ...]
    stages: tuple[WorkflowStageRun, ...]
    status: Literal["active", "completed", "blocked"] = "active"
    current_stage_id: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """A bounded overview of a session's completed work."""

    text: str
    generation: Literal["deterministic", "llm"]
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Provider-neutral JSON-schema definition for one tool."""

    name: str
    description: str
    parameters: dict[str, object]


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """A user's decision about one proposed terminal command."""

    approved: bool
    feedback: str = ""


@dataclass(frozen=True, slots=True)
class CommandExecution:
    """Captured result of a PowerShell process."""

    status: Literal["completed", "failed", "timed_out"]
    exit_code: int | None
    stdout: str
    timed_out: bool = False
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Result of evaluating an operation against a guardrail."""

    allowed: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One observable model or tool lifecycle event."""

    sequence: int
    call_number: int
    kind: ProgressEventKind
    summary: str
    target: str
    status: ProgressStatus
    duration_ms: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    request_number: int | None = None
    tags: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    usage_source: Literal["provider", "estimated", "unknown"] = "unknown"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class Session:
    """A resumable conversation and its execution history."""

    session_id: str
    workspace: str
    model: str
    max_turns_override: int | None = None
    token_budget_override: int | None = None
    summary: SessionSummary | None = None
    messages: list[Message] = field(default_factory=list)
    events: list[ProgressEvent] = field(default_factory=list)
    plans: list[TaskPlan] = field(default_factory=list)
    evidence: list[CompletionEvidence] = field(default_factory=list)
    workflows: list[WorkflowRun] = field(default_factory=list)
    pending_workflow_override: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    schema_version: int = 7

    def touch(self) -> None:
        """Refresh the session update timestamp."""
        self.updated_at = datetime.now(UTC).isoformat()
