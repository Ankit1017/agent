"""Protected one-call model workflow for saved speaking-avatar conversations."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping, Sequence

from local_harness.application.answer_quality import normalize_assistant_markdown
from local_harness.application.ports import ModelClient, VoiceConversationRepository
from local_harness.domain.errors import (
    ModelError,
    VoiceConversationBusyError,
    VoiceConversationValidationError,
)
from local_harness.domain.models import Message, ModelCompletion
from local_harness.domain.voice_agent import VoiceAgentSnapshot
from local_harness.domain.voice_conversation import (
    VoiceConversation,
    VoiceConversationMessage,
    VoiceConversationTurn,
)
from local_harness.identifiers import new_session_id

_SYSTEM_PROMPT = (
    "You are a concise local voice-conversation assistant. Reply in the language used by the "
    "user's latest message. Use safe, readable Markdown suitable for display, but prefer natural "
    "conversational sentences that sound good when spoken. Keep the answer under 1500 characters. "
    "You have no tools, files, workspace context, web access, or ability to take actions; never "
    "claim otherwise. Do not reveal or request secrets."
)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MARKDOWN_MARKS = re.compile(r"[`*_>#~|]+")
_MAX_MESSAGES = 1_000


class VoiceConversationService:
    """Manage redacted transcripts and make exactly one tool-free model call per turn."""

    def __init__(
        self,
        repository: VoiceConversationRepository,
        models: Mapping[str, ModelClient],
        sanitizer: Callable[[str], tuple[str, bool]],
        *,
        default_model: str,
        context_max_chars: int,
        max_input_chars: int = 5_000,
        max_reply_chars: int = 1_500,
    ) -> None:
        """Bind protected storage, exact model aliases, and deterministic limits."""
        if default_model not in models:
            raise ValueError("Default voice-conversation model is not configured")
        self._repository = repository
        self._models = dict(models)
        self._sanitizer = sanitizer
        self._default_model = default_model
        self._context_max_chars = context_max_chars
        self._max_input_chars = max_input_chars
        self._max_reply_chars = max_reply_chars
        self._generation = threading.Lock()

    @property
    def models(self) -> tuple[str, ...]:
        """Return configured aliases in composition order."""
        return tuple(self._models)

    @property
    def default_model(self) -> str:
        """Return the model used for new conversations."""
        return self._default_model

    def create(self, model: str | None = None) -> VoiceConversation:
        """Create one empty conversation with an exact configured model alias."""
        self._acquire_generation()
        try:
            selected = model or self._default_model
            self._validate_model(selected)
            conversation = VoiceConversation(new_session_id(), "New conversation", selected)
            self._repository.save(conversation)
            return conversation
        finally:
            self._generation.release()

    def create_agent(self, snapshot: VoiceAgentSnapshot, session_id: str) -> VoiceConversation:
        """Create agent-backed conversation metadata with an immutable profile snapshot."""
        self._acquire_generation()
        try:
            self._validate_model(snapshot.model)
            conversation = VoiceConversation(
                new_session_id(),
                "New conversation",
                snapshot.model,
                agent_snapshot=snapshot,
                agent_session_id=session_id,
                schema_version=2,
            )
            self._repository.save(conversation)
            return conversation
        finally:
            self._generation.release()

    def upgrade_agent(
        self, conversation_id: str, snapshot: VoiceAgentSnapshot
    ) -> VoiceConversation:
        """Apply an explicit same-workspace profile revision between turns."""
        self._acquire_generation()
        try:
            conversation = self._repository.load(conversation_id)
            current = conversation.agent_snapshot
            if current is None:
                raise VoiceConversationValidationError(
                    "Protected conversations cannot be converted in place"
                )
            if current.workspace_id != snapshot.workspace_id:
                raise VoiceConversationValidationError(
                    "A workspace change requires a new conversation"
                )
            self._validate_model(snapshot.model)
            conversation.agent_snapshot = snapshot
            conversation.model = snapshot.model
            conversation.touch()
            self._repository.save(conversation)
            return conversation
        finally:
            self._generation.release()

    def list_conversations(self) -> list[VoiceConversation]:
        """Return saved conversations newest first."""
        return self._repository.list_conversations()

    def load(self, conversation_id: str) -> VoiceConversation:
        """Load one saved conversation."""
        return self._repository.load(conversation_id)

    def update(
        self, conversation_id: str, *, title: str | None = None, model: str | None = None
    ) -> VoiceConversation:
        """Rename or switch the exact model between turns."""
        self._acquire_generation()
        try:
            conversation = self._repository.load(conversation_id)
            if title is not None:
                safe_title, _ = self._sanitizer(title.strip())
                if not safe_title or len(safe_title) > 80:
                    raise VoiceConversationValidationError(
                        "Conversation title must contain 1 to 80 characters"
                    )
                conversation.title = safe_title
            if model is not None:
                if conversation.agent_snapshot is not None:
                    raise VoiceConversationValidationError(
                        "Agent conversation models are changed through a profile upgrade"
                    )
                self._validate_model(model)
                conversation.model = model
            if title is None and model is None:
                raise VoiceConversationValidationError("No conversation update was supplied")
            conversation.touch()
            self._repository.save(conversation)
            return conversation
        finally:
            self._generation.release()

    def delete(self, conversation_id: str, confirmation: str) -> None:
        """Delete only after an exact identifier confirmation."""
        self._acquire_generation()
        try:
            if confirmation != conversation_id:
                raise VoiceConversationValidationError(
                    "Conversation deletion confirmation did not match"
                )
            self._repository.delete(conversation_id)
        finally:
            self._generation.release()

    def complete_turn(self, conversation_id: str, text: str) -> VoiceConversationTurn:
        """Sanitize one input, call the selected model once, and atomically save the turn."""
        normalized = text.strip()
        if not normalized or len(normalized) > self._max_input_chars:
            raise VoiceConversationValidationError(
                f"Message must contain 1 to {self._max_input_chars} characters"
            )
        safe_input, input_redacted = self._sanitizer(normalized)
        self._acquire_generation()
        try:
            conversation = self._repository.load(conversation_id)
            if conversation.agent_snapshot is not None:
                raise VoiceConversationValidationError(
                    "Agent conversations require the bounded agent-turn endpoint"
                )
            if len(conversation.messages) + 2 > _MAX_MESSAGES:
                raise VoiceConversationValidationError(
                    "This conversation reached its 1000-message storage limit"
                )
            model = self._models.get(conversation.model)
            if model is None:
                raise VoiceConversationValidationError("Conversation model is not configured")
            payload = self._provider_messages(conversation.messages, safe_input)
            completion = model.complete(payload, ())
            result = completion.message if isinstance(completion, ModelCompletion) else completion
            if result.tool_calls:
                raise ModelError("Voice conversation returned an unsupported tool request")
            if result.role != "assistant" or not result.content:
                raise ModelError("Voice conversation returned no answer")
            safe_reply, reply_redacted = self._sanitizer(result.content)
            safe_reply = re.sub(r"<[^>]{1,200}>", "", safe_reply)
            reply = normalize_assistant_markdown(safe_reply)[: self._max_reply_chars].rstrip()
            if not reply:
                raise ModelError("Voice conversation returned no usable answer")
            user_message = VoiceConversationMessage(new_session_id(), "user", safe_input)
            assistant_message = VoiceConversationMessage(new_session_id(), "assistant", reply)
            if not conversation.messages:
                conversation.title = _title_from_message(safe_input)
            conversation.messages.extend((user_message, assistant_message))
            conversation.touch()
            self._repository.save(conversation)
            usage = completion.usage if isinstance(completion, ModelCompletion) else None
            return VoiceConversationTurn(
                conversation,
                user_message,
                assistant_message,
                markdown_to_speech_text(reply),
                input_redacted or reply_redacted,
                usage.input_tokens if usage else None,
                usage.output_tokens if usage else None,
            )
        finally:
            self._generation.release()

    def _provider_messages(
        self, history: Sequence[VoiceConversationMessage], current: str
    ) -> tuple[Message, ...]:
        fixed = len(_SYSTEM_PROMPT) + len(current)
        if fixed > self._context_max_chars:
            raise VoiceConversationValidationError("Message cannot fit the model context limit")
        budget = self._context_max_chars - fixed
        selected: list[VoiceConversationMessage] = []
        used = 0
        for item in reversed(history):
            cost = len(item.content)
            if used + cost > budget:
                break
            selected.append(item)
            used += cost
        selected.reverse()
        messages = [Message("system", _SYSTEM_PROMPT)]
        messages.extend(Message(item.role, item.content) for item in selected)
        messages.append(Message("user", current))
        return tuple(messages)

    def _validate_model(self, model: str) -> None:
        if model not in self._models:
            raise VoiceConversationValidationError("Model is not configured")

    def _acquire_generation(self) -> None:
        if not self._generation.acquire(blocking=False):
            raise VoiceConversationBusyError("Another voice conversation is generating a reply")


def markdown_to_speech_text(value: str) -> str:
    """Derive bounded natural text from display Markdown without persisting audio."""
    text = re.sub(r"<[^>]{1,200}>", "", value)
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _MARKDOWN_MARKS.sub(" ", text)
    text = re.sub(r"(?m)^\s*[-+]\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:1_500]


def _title_from_message(value: str) -> str:
    title = re.sub(r"\s+", " ", value).strip()
    return title[:60].rstrip() or "New conversation"
