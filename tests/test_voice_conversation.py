"""Offline tests for protected model-only voice conversations."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_harness.application.voice_conversation import VoiceConversationService
from local_harness.domain.errors import (
    ModelError,
    VoiceConversationBusyError,
    VoiceConversationNotFoundError,
    VoiceConversationValidationError,
)
from local_harness.domain.models import Message, ModelCompletion, ToolCall, ToolDefinition
from local_harness.domain.voice_conversation import VoiceConversationMessage
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.voice_conversations import JsonVoiceConversationRepository
from local_harness.interfaces.web.api import create_app
from local_harness.interfaces.web.coordinator import WebRuntimeCoordinator


class RecordingModel:
    """Record calls and return one deterministic Markdown answer."""

    def __init__(self, answer: str = "**Hello** from the voice bot") -> None:
        self.answer = answer
        self.calls: list[tuple[Sequence[Message], Sequence[ToolDefinition]]] = []

    def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelCompletion:
        """Record exactly what the protected workflow submitted."""
        self.calls.append((messages, tools))
        return ModelCompletion(Message("assistant", self.answer))


class BlockingModel(RecordingModel):
    """Hold one model call open for concurrency testing."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelCompletion:
        """Wait until the test permits the one active request to complete."""
        self.started.set()
        self.release.wait(5)
        return super().complete(messages, tools)


def _service(
    tmp_path: Path,
    model: RecordingModel | None = None,
    *,
    context_max_chars: int = 30_000,
) -> tuple[VoiceConversationService, JsonVoiceConversationRepository, RecordingModel]:
    redactor = SecretRedactor(("configured-secret",))
    repository = JsonVoiceConversationRepository(tmp_path, redactor)
    selected = model or RecordingModel()
    service = VoiceConversationService(
        repository,
        {"model-a": selected, "model-b": RecordingModel("second")},
        redactor.sanitize,
        default_model="model-a",
        context_max_chars=context_max_chars,
    )
    return service, repository, selected


def _coordinator(tmp_path: Path) -> tuple[WebRuntimeCoordinator, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    tmp_path.joinpath(".env").write_text(
        "OPENAI_API_KEY=sk-local-real-test-key\n"
        "OPENAI_BASE_URL=http://127.0.0.1:4000/v1\n"
        "OPENAI_MODEL=model-a\n"
        "HARNESS_MODELS=model-a,model-b\n"
        "SEARXNG_BASE_URL=http://127.0.0.1:8080\n",
        encoding="utf-8",
    )
    static = tmp_path / "static"
    static.mkdir()
    static.joinpath("index.html").write_text("<main>Harness</main>", encoding="utf-8")
    return WebRuntimeCoordinator(tmp_path, tmp_path / "catalog.json"), static


def test_turn_is_one_tool_free_redacted_call_and_atomic_save(tmp_path: Path) -> None:
    """A successful turn uses no agent context and persists only sanitized text."""
    service, repository, model = _service(tmp_path)
    conversation = service.create()
    turn = service.complete_turn(
        conversation.conversation_id, "Use configured-secret and sk-abcdefghijklmnop"
    )

    assert len(model.calls) == 1
    messages, tools = model.calls[0]
    assert tools == ()
    assert [message.role for message in messages] == ["system", "user"]
    assert "no tools" in (messages[0].content or "").casefold()
    assert "workspace" in (messages[0].content or "").casefold()
    assert "language" in (messages[0].content or "").casefold()
    assert "configured-secret" not in (messages[-1].content or "")
    assert turn.redacted is True
    assert turn.speech_text == "Hello from the voice bot"
    saved = repository.load(conversation.conversation_id)
    assert len(saved.messages) == 2
    assert saved.title.startswith("Use [REDACTED]")
    assert "configured-secret" not in saved.messages[0].content
    assert not list((tmp_path / ".harness").rglob("*.wav"))


def test_reply_is_normalized_redacted_and_bounded_for_speech(tmp_path: Path) -> None:
    """Assistant text is safe, concise, and usable for display and speech."""
    model = RecordingModel("<script>bad</script> **Answer** configured-secret " + "x" * 2_000)
    service, repository, _ = _service(tmp_path, model)
    conversation = service.create()

    turn = service.complete_turn(conversation.conversation_id, "hello")

    assert len(turn.assistant_message.content) == 1_500
    assert "<script>" not in turn.assistant_message.content
    assert "configured-secret" not in turn.assistant_message.content
    assert "**" not in turn.speech_text
    assert repository.load(conversation.conversation_id).messages[-1].content == (
        turn.assistant_message.content
    )


def test_model_failure_or_tool_call_never_saves_partial_turn(tmp_path: Path) -> None:
    """Only a complete usable assistant answer commits the user/assistant pair."""
    service, repository, model = _service(tmp_path)
    conversation = service.create()
    model.answer = ""
    with pytest.raises(ModelError, match="no answer"):
        service.complete_turn(conversation.conversation_id, "unsaved")
    assert repository.load(conversation.conversation_id).messages == []

    class ToolModel(RecordingModel):
        def complete(
            self, messages: Sequence[Message], tools: Sequence[ToolDefinition]
        ) -> ModelCompletion:
            return ModelCompletion(
                Message("assistant", None, (ToolCall("call", "read_file", "{}"),))
            )

    tool_service, tool_repository, _ = _service(tmp_path / "tool", ToolModel())
    tool_conversation = tool_service.create()
    with pytest.raises(ModelError, match="tool"):
        tool_service.complete_turn(tool_conversation.conversation_id, "do not execute")
    assert tool_repository.load(tool_conversation.conversation_id).messages == []


def test_history_is_newest_bounded_and_full_transcript_remains(tmp_path: Path) -> None:
    """Provider context drops oldest whole messages without deleting saved history."""
    service, repository, model = _service(tmp_path, context_max_chars=650)
    conversation = service.create()
    conversation.messages.extend(
        [
            VoiceConversationMessage("a" * 32, "user", "old-" + "x" * 120),
            VoiceConversationMessage("b" * 32, "assistant", "new-" + "y" * 120),
        ]
    )
    repository.save(conversation)
    service.complete_turn(conversation.conversation_id, "latest")
    submitted = model.calls[0][0]
    combined = " ".join(message.content or "" for message in submitted)
    assert "old-" not in combined
    assert "new-" in combined
    assert len(repository.load(conversation.conversation_id).messages) == 4


def test_crud_model_allowlist_pagination_and_exact_delete(tmp_path: Path) -> None:
    """Saved conversations support bounded metadata changes and exact deletion."""
    service, repository, _ = _service(tmp_path)
    conversation = service.create("model-b")
    assert service.update(conversation.conversation_id, title="Renamed").title == "Renamed"
    assert service.update(conversation.conversation_id, model="model-a").model == "model-a"
    with pytest.raises(VoiceConversationValidationError, match="Model"):
        service.update(conversation.conversation_id, model="arbitrary")
    with pytest.raises(VoiceConversationValidationError, match="confirmation"):
        service.delete(conversation.conversation_id, "wrong")
    service.delete(conversation.conversation_id, conversation.conversation_id)
    with pytest.raises(VoiceConversationNotFoundError):
        repository.load(conversation.conversation_id)


def test_service_rejects_invalid_configuration_updates_and_bounds(tmp_path: Path) -> None:
    """Configuration and request limits fail before any provider call or partial write."""
    service, repository, model = _service(tmp_path)
    conversation = service.create()
    assert service.models == ("model-a", "model-b")
    assert service.default_model == "model-a"
    assert (
        service.load(conversation.conversation_id).conversation_id == conversation.conversation_id
    )
    assert len(service.list_conversations()) == 1

    with pytest.raises(ValueError, match="Default"):
        VoiceConversationService(
            repository,
            {"model-a": model},
            SecretRedactor(()).sanitize,
            default_model="missing",
            context_max_chars=30_000,
        )
    with pytest.raises(VoiceConversationValidationError, match="update"):
        service.update(conversation.conversation_id)
    with pytest.raises(VoiceConversationValidationError, match="title"):
        service.update(conversation.conversation_id, title="   ")
    with pytest.raises(VoiceConversationValidationError, match="Message"):
        service.complete_turn(conversation.conversation_id, "   ")
    with pytest.raises(VoiceConversationValidationError, match="Message"):
        service.complete_turn(conversation.conversation_id, "x" * 5_001)

    constrained, _, constrained_model = _service(tmp_path / "context", context_max_chars=1)
    constrained_conversation = constrained.create()
    with pytest.raises(VoiceConversationValidationError, match="context"):
        constrained.complete_turn(constrained_conversation.conversation_id, "hello")
    assert constrained_model.calls == []


def test_repository_skips_corruption_and_rejects_invalid_identifiers(tmp_path: Path) -> None:
    """Transcript discovery remains bounded when protected state contains bad documents."""
    service, repository, _ = _service(tmp_path)
    assert repository.list_conversations() == []
    conversation = service.create()
    directory = tmp_path / ".harness" / "voice-conversations"
    directory.joinpath("f" * 32 + ".json").write_text("{broken", encoding="utf-8")

    assert [item.conversation_id for item in repository.list_conversations()] == [
        conversation.conversation_id
    ]
    with pytest.raises(VoiceConversationValidationError, match="identifier"):
        repository.load("../outside")
    with pytest.raises(VoiceConversationNotFoundError):
        repository.delete("e" * 32)


def test_generation_is_globally_non_queuing(tmp_path: Path) -> None:
    """A concurrent turn fails quickly instead of entering an unbounded queue."""
    model = BlockingModel()
    service, _, _ = _service(tmp_path, model)
    first = service.create()
    second = service.create()
    worker = threading.Thread(
        target=service.complete_turn, args=(first.conversation_id, "first"), daemon=True
    )
    worker.start()
    assert model.started.wait(2)
    with pytest.raises(VoiceConversationBusyError):
        service.complete_turn(second.conversation_id, "second")
    model.release.set()
    worker.join(3)


def test_protected_voice_conversation_api(tmp_path: Path) -> None:
    """Transcript reads require a browser and mutations retain CSRF and Origin controls."""
    coordinator, static = _coordinator(tmp_path)
    service, _, model = _service(tmp_path / "store")
    app = create_app(
        coordinator,
        static,
        voice_conversation_service=service,
        origins=frozenset({"http://testserver"}),
        trusted_hosts=["testserver"],
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/speech/conversations").status_code == 403
        bootstrap = client.get("/api/v1/bootstrap").json()
        headers = {
            "Origin": "http://testserver",
            "X-Harness-CSRF": bootstrap["csrf_token"],
        }
        assert bootstrap["voice_conversation_enabled"] is True
        assert client.post("/api/v1/speech/conversations", json={}).status_code == 403
        created = client.post(
            "/api/v1/speech/conversations", headers=headers, json={"model": "model-a"}
        )
        assert created.status_code == 200
        conversation_id = created.json()["conversation_id"]
        assert client.get("/api/v1/speech/conversations").json()[0]["message_count"] == 0
        assert (
            client.post(
                f"/api/v1/speech/conversations/{conversation_id}/turns",
                headers=headers,
                json={"text": "hello", "extra": True},
            ).status_code
            == 422
        )
        turn = client.post(
            f"/api/v1/speech/conversations/{conversation_id}/turns",
            headers=headers,
            json={"text": "hello"},
        )
        assert turn.status_code == 200
        assert turn.json()["speech_text"] == "Hello from the voice bot"
        assert len(model.calls) == 1
        detail = client.get(
            f"/api/v1/speech/conversations/{conversation_id}?offset=0&limit=1"
        ).json()
        assert len(detail["messages"]) == 1
        assert detail["has_older_messages"] is True
        assert (
            client.patch(
                f"/api/v1/speech/conversations/{conversation_id}",
                headers=headers,
                json={"title": "Protected voice chat"},
            ).json()["title"]
            == "Protected voice chat"
        )
        deleted = client.request(
            "DELETE",
            f"/api/v1/speech/conversations/{conversation_id}",
            headers=headers,
            json={"confirmation": conversation_id},
        )
        assert deleted.json() == {"deleted": True}
