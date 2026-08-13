"""Atomic workspace-local JSON storage for protected voice conversations."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from typing import cast

from local_harness.domain.errors import (
    VoiceConversationNotFoundError,
    VoiceConversationStorageError,
    VoiceConversationValidationError,
)
from local_harness.domain.voice_agent import VoiceAgentSnapshot
from local_harness.domain.voice_conversation import (
    VoiceConversation,
    VoiceConversationMessage,
    VoiceConversationRole,
)
from local_harness.guardrails.redaction import SecretRedactor

_ID = re.compile(r"^[a-f0-9]{32}$")
_MAX_MESSAGES = 1_000


class JsonVoiceConversationRepository:
    """Persist schema-v1 redacted text transcripts below protected harness state."""

    def __init__(self, workspace: Path, redactor: SecretRedactor) -> None:
        """Bind the repository to the control workspace and redaction policy."""
        self._directory = workspace / ".harness" / "voice-conversations"
        self._redactor = redactor
        self._lock = threading.RLock()

    def save(self, conversation: VoiceConversation) -> None:
        """Redact and atomically replace one validated transcript."""
        self._validate_id(conversation.conversation_id)
        if len(conversation.messages) > _MAX_MESSAGES:
            raise VoiceConversationValidationError("Voice conversation is too large")
        with self._lock:
            try:
                self._directory.mkdir(parents=True, exist_ok=True)
                handle, temporary_name = tempfile.mkstemp(
                    prefix=f".{conversation.conversation_id}.",
                    suffix=".tmp",
                    dir=self._directory,
                )
                try:
                    with os.fdopen(handle, "w", encoding="utf-8") as stream:
                        json.dump(
                            _to_dict(conversation, self._redactor),
                            stream,
                            ensure_ascii=False,
                            indent=2,
                        )
                        stream.write("\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary_name, self._path(conversation.conversation_id))
                except Exception:
                    Path(temporary_name).unlink(missing_ok=True)
                    raise
            except (OSError, TypeError, ValueError) as exc:
                raise VoiceConversationStorageError(
                    "Could not save the voice conversation"
                ) from exc

    def load(self, conversation_id: str) -> VoiceConversation:
        """Load one supported, bounded transcript document."""
        self._validate_id(conversation_id)
        with self._lock:
            try:
                payload = json.loads(self._path(conversation_id).read_text(encoding="utf-8"))
                return _from_dict(payload)
            except FileNotFoundError as exc:
                raise VoiceConversationNotFoundError("Voice conversation was not found") from exc
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise VoiceConversationStorageError(
                    "Voice conversation is corrupt or unsupported"
                ) from exc

    def list_conversations(self) -> list[VoiceConversation]:
        """Return valid conversations newest first while skipping corrupt documents."""
        with self._lock:
            try:
                if not self._directory.exists():
                    return []
                conversations: list[VoiceConversation] = []
                for path in self._directory.glob("*.json"):
                    try:
                        conversations.append(self.load(path.stem))
                    except (VoiceConversationStorageError, VoiceConversationValidationError):
                        continue
                return sorted(conversations, key=lambda item: item.updated_at, reverse=True)
            except OSError as exc:
                raise VoiceConversationStorageError("Could not list voice conversations") from exc

    def delete(self, conversation_id: str) -> None:
        """Permanently delete one exact transcript document."""
        self._validate_id(conversation_id)
        with self._lock:
            try:
                self._path(conversation_id).unlink()
            except FileNotFoundError as exc:
                raise VoiceConversationNotFoundError("Voice conversation was not found") from exc
            except OSError as exc:
                raise VoiceConversationStorageError(
                    "Could not delete the voice conversation"
                ) from exc

    def _path(self, conversation_id: str) -> Path:
        return self._directory / f"{conversation_id}.json"

    @staticmethod
    def _validate_id(conversation_id: str) -> None:
        if not _ID.fullmatch(conversation_id):
            raise VoiceConversationValidationError("Invalid voice conversation identifier")


def _to_dict(conversation: VoiceConversation, redactor: SecretRedactor) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 2 if conversation.agent_snapshot else 1,
        "conversation_id": conversation.conversation_id,
        "title": redactor.redact(conversation.title)[:80],
        "model": conversation.model,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "messages": [
            {
                "message_id": item.message_id,
                "role": item.role,
                "content": redactor.redact(item.content)[:5_000],
                "created_at": item.created_at,
            }
            for item in conversation.messages
        ],
    }
    if conversation.agent_snapshot is not None:
        snapshot = asdict(conversation.agent_snapshot)
        snapshot["name"] = redactor.redact(conversation.agent_snapshot.name)[:80]
        snapshot["instructions"] = redactor.redact(conversation.agent_snapshot.instructions)[:4_000]
        snapshot["allowed_tools"] = list(conversation.agent_snapshot.allowed_tools)
        payload["agent_snapshot"] = snapshot
        payload["agent_session_id"] = conversation.agent_session_id
    return payload


def _from_dict(value: object) -> VoiceConversation:
    if not isinstance(value, dict) or value.get("schema_version") not in {1, 2}:
        raise ValueError("Unsupported voice-conversation schema")
    messages_value = value["messages"]
    if not isinstance(messages_value, list) or len(messages_value) > _MAX_MESSAGES:
        raise ValueError("Invalid voice-conversation messages")
    messages: list[VoiceConversationMessage] = []
    for raw in messages_value:
        if not isinstance(raw, dict):
            raise ValueError("Invalid voice-conversation message")
        role = raw["role"]
        content = raw["content"]
        if role not in {"user", "assistant"} or not isinstance(content, str):
            raise ValueError("Invalid voice-conversation message")
        if len(content) > 5_000:
            raise ValueError("Voice-conversation message exceeds its bound")
        messages.append(
            VoiceConversationMessage(
                str(raw["message_id"]),
                cast(VoiceConversationRole, role),
                content,
                str(raw["created_at"]),
            )
        )
        JsonVoiceConversationRepository._validate_id(messages[-1].message_id)
    metadata = (
        value["conversation_id"],
        value["title"],
        value["model"],
        value["created_at"],
        value["updated_at"],
    )
    if not all(isinstance(item, str) for item in metadata):
        raise ValueError("Invalid voice-conversation metadata")
    conversation = VoiceConversation(
        conversation_id=cast(str, metadata[0]),
        title=cast(str, metadata[1]),
        model=cast(str, metadata[2]),
        messages=messages,
        created_at=cast(str, metadata[3]),
        updated_at=cast(str, metadata[4]),
        schema_version=int(value["schema_version"]),
    )
    if value.get("schema_version") == 2:
        raw_snapshot = value.get("agent_snapshot")
        session_id = value.get("agent_session_id")
        if not isinstance(raw_snapshot, dict) or not isinstance(session_id, str):
            raise ValueError("Invalid voice-agent conversation metadata")
        snapshot_value = dict(raw_snapshot)
        tools = snapshot_value.get("allowed_tools")
        if not isinstance(tools, list) or not all(isinstance(item, str) for item in tools):
            raise ValueError("Invalid voice-agent snapshot tools")
        snapshot_value["allowed_tools"] = tuple(tools)
        conversation.agent_snapshot = VoiceAgentSnapshot(**snapshot_value)
        conversation.agent_session_id = session_id
        JsonVoiceConversationRepository._validate_id(session_id)
    JsonVoiceConversationRepository._validate_id(conversation.conversation_id)
    if not 1 <= len(conversation.title) <= 80 or len(conversation.model) > 128:
        raise ValueError("Invalid voice-conversation metadata")
    return conversation
