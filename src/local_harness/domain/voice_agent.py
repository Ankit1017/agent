"""Provider-neutral configuration values for reusable voice agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

VoiceAgentWorkflowMode = Literal["off", "auto"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class VoiceAgentProfileSpec:
    """Closed user-editable values for creating or revising a profile."""

    name: str
    instructions: str
    workspace_id: str
    model: str
    allowed_tools: tuple[str, ...]
    project_context_enabled: bool
    workflow_mode: VoiceAgentWorkflowMode
    max_turns: int
    token_budget: int
    context_max_chars: int
    max_answer_chars: int
    tool_schema_limit: int
    tool_activation_limit: int
    voice_id: str
    speaking_rate: float
    auto_speak: bool


@dataclass(frozen=True, slots=True)
class VoiceAgentSnapshot:
    """Immutable execution and speech policy captured by one conversation."""

    profile_id: str
    revision: int
    name: str
    instructions: str
    workspace_id: str
    model: str
    allowed_tools: tuple[str, ...]
    project_context_enabled: bool
    workflow_mode: VoiceAgentWorkflowMode
    max_turns: int
    token_budget: int
    context_max_chars: int
    max_answer_chars: int
    tool_schema_limit: int
    tool_activation_limit: int
    voice_id: str
    speaking_rate: float
    auto_speak: bool


@dataclass(slots=True)
class VoiceAgentProfile:
    """One sanitized reusable voice-agent configuration."""

    profile_id: str
    revision: int
    name: str
    instructions: str
    workspace_id: str
    model: str
    allowed_tools: tuple[str, ...]
    project_context_enabled: bool = True
    workflow_mode: VoiceAgentWorkflowMode = "off"
    max_turns: int = 8
    token_budget: int = 0
    context_max_chars: int = 30_000
    max_answer_chars: int = 1_500
    tool_schema_limit: int = 8
    tool_activation_limit: int = 5
    voice_id: str = ""
    speaking_rate: float = 1.0
    auto_speak: bool = True
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    schema_version: int = 1

    def snapshot(self) -> VoiceAgentSnapshot:
        """Return the immutable settings used by a new conversation."""
        return VoiceAgentSnapshot(
            self.profile_id,
            self.revision,
            self.name,
            self.instructions,
            self.workspace_id,
            self.model,
            self.allowed_tools,
            self.project_context_enabled,
            self.workflow_mode,
            self.max_turns,
            self.token_budget,
            self.context_max_chars,
            self.max_answer_chars,
            self.tool_schema_limit,
            self.tool_activation_limit,
            self.voice_id,
            self.speaking_rate,
            self.auto_speak,
        )

    def touch(self) -> None:
        """Advance the revision and sortable update timestamp."""
        self.revision += 1
        self.updated_at = _now()


@dataclass(frozen=True, slots=True)
class VoiceAgentExecutionPolicy:
    """Validated snapshot projected into the generic agent runtime."""

    instructions: str
    allowed_tools: tuple[str, ...]
    project_context_enabled: bool
    workflow_mode: VoiceAgentWorkflowMode
    max_turns: int
    token_budget: int
    context_max_chars: int
    max_answer_chars: int
    tool_schema_limit: int
    tool_activation_limit: int
