"""Provider-neutral values for protected model-only voice conversations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from local_harness.domain.voice_agent import VoiceAgentSnapshot

VoiceConversationRole = Literal["user", "assistant"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class VoiceConversationMessage:
    """One sanitized text message in a voice conversation."""

    message_id: str
    role: VoiceConversationRole
    content: str
    created_at: str = field(default_factory=_now)


@dataclass(slots=True)
class VoiceConversation:
    """One independent saved text conversation for the speaking avatar."""

    conversation_id: str
    title: str
    model: str
    messages: list[VoiceConversationMessage] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    schema_version: int = 1
    agent_snapshot: VoiceAgentSnapshot | None = None
    agent_session_id: str = ""

    def touch(self) -> None:
        """Refresh the sortable update timestamp."""
        self.updated_at = _now()


@dataclass(frozen=True, slots=True)
class VoiceConversationTurn:
    """One atomically completed user/assistant exchange."""

    conversation: VoiceConversation
    user_message: VoiceConversationMessage
    assistant_message: VoiceConversationMessage
    speech_text: str
    redacted: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
