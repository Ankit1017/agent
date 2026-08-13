"""FastAPI application exposing the local harness browser contract."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal, cast

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
)
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from local_harness.application.answer_quality import normalize_assistant_markdown
from local_harness.application.session_services import session_info
from local_harness.application.speech import SpeechService
from local_harness.application.speech_input import SpeechInputService, SpeechInputSession
from local_harness.application.tool_routing import RequestToolRouter
from local_harness.application.voice_agent_profiles import VoiceAgentProfileService
from local_harness.application.voice_conversation import (
    VoiceConversationService,
    markdown_to_speech_text,
)
from local_harness.application.workflows import WorkflowCatalog
from local_harness.domain.errors import (
    HarnessError,
    ModelError,
    SessionError,
    SpeechBusyError,
    SpeechInputBusyError,
    SpeechInputUnavailableError,
    SpeechInputValidationError,
    SpeechUnavailableError,
    SpeechValidationError,
    VoiceAgentProfileError,
    VoiceConversationBusyError,
    VoiceConversationNotFoundError,
    VoiceConversationStorageError,
    VoiceConversationValidationError,
)
from local_harness.domain.evaluation import HarnessCandidate
from local_harness.domain.maintenance import ArchiveInfo
from local_harness.domain.models import Session
from local_harness.domain.project_memory import ProjectIndexStatus, RetrievedProjectContext
from local_harness.domain.voice_agent import (
    VoiceAgentExecutionPolicy,
    VoiceAgentProfile,
    VoiceAgentProfileSpec,
    VoiceAgentSnapshot,
)
from local_harness.domain.voice_conversation import (
    VoiceConversation,
    VoiceConversationMessage,
    VoiceConversationTurn,
)
from local_harness.identifiers import new_session_id
from local_harness.interfaces.commands import parse_command
from local_harness.interfaces.web.coordinator import WebRuntimeCoordinator, serialize_task
from local_harness.interfaces.web.events import WebEventHub

_COOKIE = "harness_browser"
_BODY_LIMIT = 131_072
_STREAM_END = object()


def _next_speech_chunk(chunks: Iterator[bytes]) -> bytes | object:
    """Pull one provider chunk without allowing ``StopIteration`` through a future."""
    return next(chunks, _STREAM_END)


async def _speech_chunk_bridge(chunks: Iterator[bytes], first: bytes) -> AsyncIterator[bytes]:
    """Pull at most one speech chunk off-loop and close the provider on cancellation."""
    try:
        yield first
        while True:
            chunk = await asyncio.to_thread(_next_speech_chunk, chunks)
            if chunk is _STREAM_END:
                return
            yield cast(bytes, chunk)
    finally:
        close = getattr(chunks, "close", None)
        if callable(close):
            await asyncio.to_thread(close)


class _ClosedModel(BaseModel):
    """Reject unknown browser API fields."""

    model_config = ConfigDict(extra="forbid")


class WorkspaceProposal(_ClosedModel):
    """A requested workspace label and absolute path."""

    label: str = Field(min_length=1, max_length=80)
    path: str = Field(min_length=1, max_length=1_024)


class WorkspaceConfirmation(_ClosedModel):
    """Explicit confirmation of one validated workspace challenge."""

    challenge_id: str
    approved: bool


class PromptRequest(_ClosedModel):
    """One browser-submitted agent prompt."""

    prompt: str = Field(min_length=1, max_length=60_000)
    client_id: str = Field(min_length=16, max_length=128)
    workflow_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")


class ApprovalResolution(_ClosedModel):
    """An owning browser's explicit approval decision."""

    workspace_id: str
    client_id: str = Field(min_length=16, max_length=128)
    approved: bool
    feedback: str = Field(default="", max_length=1_000)


class LimitUpdate(_ClosedModel):
    """A session limit override or reset request."""

    value: int | None = None


class ModelUpdate(_ClosedModel):
    """A configured model alias for one idle session, or null for the default."""

    model: str | None = Field(default=None, max_length=128)


class WorkflowOverride(_ClosedModel):
    """One one-shot workflow override, or null to restore automatic selection."""

    workflow_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")


class EvaluationMark(_ClosedModel):
    """An explicit user judgement for one evaluated request."""

    request_number: int = Field(ge=1)
    outcome: Literal["pass", "fail"]
    note: str = Field(default="", max_length=500)


class CandidateProposalRequest(_ClosedModel):
    """An explicit candidate-proposal model call."""

    client_id: str = Field(min_length=16, max_length=128)
    component_id: str = Field(default="", pattern=r"^[a-z_]{0,64}$")


class CandidateDecision(_ClosedModel):
    """A non-executing approval or rejection of a proposal."""

    approved: bool
    feedback: str = Field(default="", max_length=500)


class TagUpdate(_ClosedModel):
    """One event-tag mutation."""

    label: str


class ExportRequest(_ClosedModel):
    """One session export request."""

    format: Literal["md", "csv"]


class AuxiliaryRequest(_ClosedModel):
    """Browser identity for an approval-capable auxiliary action."""

    client_id: str = Field(min_length=16, max_length=128)


class CommandRequest(_ClosedModel):
    """One existing shared slash command from the browser composer."""

    value: str = Field(min_length=2, max_length=2_000)
    client_id: str = Field(min_length=16, max_length=128)


class SpeechSynthesisRequest(_ClosedModel):
    """One bounded browser speech synthesis request."""

    text: str = Field(min_length=1, max_length=5_000)
    voice_id: str = Field(min_length=1, max_length=128)
    rate: float = Field(default=1.0, ge=0.75, le=1.50)


class VoiceConversationCreate(_ClosedModel):
    """Create one saved model-only voice conversation."""

    model: str | None = Field(default=None, max_length=128)
    profile_id: str | None = Field(default=None, max_length=32)


class VoiceConversationUpdate(_ClosedModel):
    """Rename or switch the configured model between turns."""

    title: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = Field(default=None, max_length=128)


class VoiceConversationDelete(_ClosedModel):
    """Require exact identifier confirmation for permanent deletion."""

    confirmation: str = Field(min_length=32, max_length=32)


class VoiceConversationTurnRequest(_ClosedModel):
    """One bounded user message for a single tool-free model call."""

    text: str = Field(min_length=1, max_length=5_000)


class VoiceAgentProfileRequest(_ClosedModel):
    """Complete bounded editable profile state."""

    name: str = Field(min_length=1, max_length=80)
    instructions: str = Field(default="", max_length=4_000)
    workspace_id: str = Field(min_length=32, max_length=32)
    model: str = Field(min_length=1, max_length=128)
    allowed_tools: list[str] = Field(default_factory=list, max_length=64)
    project_context_enabled: bool = True
    workflow_mode: Literal["off", "auto"] = "off"
    max_turns: int = Field(default=8, ge=1, le=100)
    token_budget: int = Field(default=0, ge=0, le=1_000_000)
    context_max_chars: int = Field(default=30_000, ge=4_000)
    max_answer_chars: int = Field(default=1_500, ge=500, le=5_000)
    tool_schema_limit: int = Field(default=8, ge=1, le=32)
    tool_activation_limit: int = Field(default=5, ge=1, le=32)
    voice_id: str = Field(min_length=1, max_length=128)
    speaking_rate: float = Field(default=1.0, ge=0.75, le=1.5)
    auto_speak: bool = True


class VoiceAgentProfileDelete(_ClosedModel):
    """Exact confirmation for permanent profile deletion."""

    confirmation: str = Field(min_length=32, max_length=32)


class VoiceAgentProfileUpgrade(_ClosedModel):
    """Explicit profile revision applied to an idle same-workspace conversation."""

    profile_id: str = Field(min_length=32, max_length=32)
    revision: int = Field(ge=1)


class VoiceAgentTurnRequest(_ClosedModel):
    """One owner-bound agent request from a voice conversation."""

    text: str = Field(min_length=1, max_length=5_000)
    client_id: str = Field(min_length=16, max_length=128)


class TaskCancellationRequest(_ClosedModel):
    """Owner identity for cooperative task cancellation."""

    client_id: str = Field(min_length=16, max_length=128)


class SpeechInputStart(_ClosedModel):
    """Authenticate and declare one exact browser microphone stream."""

    type: Literal["start"]
    csrf_token: str = Field(min_length=16, max_length=128)
    mode: Literal["wake", "tap"]
    sample_rate: Literal[16000]
    channels: Literal[1]
    sample_width: Literal[2]
    encoding: Literal["s16le"]


class SpeechInputControl(_ClosedModel):
    """Control one already authenticated microphone session."""

    type: Literal["begin_tap", "finish", "pause", "rearm", "cancel", "close"]


class BrowserSecurity:
    """Issue local browser sessions and validate mutation requests."""

    def __init__(self, origins: frozenset[str]) -> None:
        """Create ephemeral same-origin browser session storage."""
        self.origins = origins
        self.sessions: dict[str, str] = {}

    def issue(self, current: str | None) -> tuple[str, str]:
        """Return an existing or newly generated session and CSRF token."""
        if current and current in self.sessions:
            return current, self.sessions[current]
        session_id = secrets.token_urlsafe(32)
        token = secrets.token_urlsafe(32)
        self.sessions[session_id] = token
        return session_id, token

    def validate(self, session_id: str | None, csrf: str | None, origin: str | None) -> None:
        """Require matching session, CSRF token, and exact local origin."""
        expected = self.sessions.get(session_id or "")
        if expected is None or not csrf or not secrets.compare_digest(expected, csrf):
            raise HTTPException(403, "CSRF validation failed")
        if origin not in self.origins:
            raise HTTPException(403, "Origin validation failed")

    def validate_session(self, session_id: str | None) -> None:
        """Require an issued browser cookie before exposing saved transcripts."""
        if session_id not in self.sessions:
            raise HTTPException(403, "Browser session validation failed")


def create_app(
    coordinator: WebRuntimeCoordinator,
    static_directory: Path,
    *,
    speech_service: SpeechService | None = None,
    speech_input_service: SpeechInputService | None = None,
    voice_conversation_service: VoiceConversationService | None = None,
    voice_agent_profile_service: VoiceAgentProfileService | None = None,
    origins: frozenset[str] = frozenset({"http://127.0.0.1:3000", "http://localhost:3000"}),
    trusted_hosts: list[str] | None = None,
) -> FastAPI:
    """Create the localhost-only browser application."""
    security = BrowserSecurity(origins)
    websocket_origins = " ".join(
        sorted(origin.replace("http://", "ws://", 1) for origin in origins)
    )
    challenges: dict[str, tuple[str, Path, float]] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        coordinator.bind_loop(asyncio.get_running_loop())
        yield
        coordinator.shutdown()

    app = FastAPI(title="Local AI Harness", version="1", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=trusted_hosts or ["127.0.0.1", "localhost", "127.0.0.1:3000"],
    )

    @app.middleware("http")
    async def secure_response(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > _BODY_LIMIT:
                    return Response("Request body is too large", status_code=413)
            except ValueError:
                return Response("Invalid Content-Length", status_code=400)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; connect-src 'self' {websocket_origins}; "
            "img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; worker-src 'self'; media-src 'self' blob:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def mutation_guard(
        origin: Annotated[str | None, Header()] = None,
        csrf: Annotated[str | None, Header(alias="X-Harness-CSRF")] = None,
        browser: Annotated[str | None, Cookie(alias=_COOKIE)] = None,
    ) -> None:
        security.validate(browser, csrf, origin)

    def browser_guard(
        browser: Annotated[str | None, Cookie(alias=_COOKIE)] = None,
    ) -> None:
        security.validate_session(browser)

    guarded = [Depends(mutation_guard)]
    browser_guarded = [Depends(browser_guard)]

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "healthy", "version": 1}

    @app.get("/api/v1/bootstrap")
    async def bootstrap(
        response: Response,
        browser: Annotated[str | None, Cookie(alias=_COOKIE)] = None,
    ) -> dict[str, object]:
        session_id, csrf = security.issue(browser)
        response.set_cookie(
            _COOKIE,
            session_id,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return {
            "version": 1,
            "csrf_token": csrf,
            "model": coordinator.settings.model,
            "models": list(coordinator.settings.models),
            "max_concurrent_tasks": 2,
            "speech_enabled": speech_service is not None,
            "speech_input_enabled": speech_input_service is not None,
            "speech_max_chars": coordinator.settings.tts_max_chars,
            "voice_conversation_enabled": voice_conversation_service is not None,
            "workspaces": [asdict(item) for item in coordinator.workspaces()],
        }

    @app.get("/api/v1/speech/voices")
    async def speech_voices() -> list[dict[str, object]]:
        if speech_service is None:
            raise HTTPException(503, "Local speech is disabled. Run the voice setup first.")
        return [asdict(voice) for voice in speech_service.voices()]

    @app.get("/api/v1/speech/input/status", dependencies=browser_guarded)
    async def speech_input_status() -> dict[str, object]:
        if speech_input_service is None:
            return {
                "enabled": False,
                "setup": "Run scripts/setup-speech-input.ps1, then enable local STT.",
            }
        return {
            "enabled": True,
            "wake_phrase": speech_input_service.wake_phrase,
            "languages": list(speech_input_service.languages),
            "max_seconds": speech_input_service.max_seconds,
            "silence_ms": speech_input_service.silence_ms,
            "audio_format": asdict(speech_input_service.audio_format),
        }

    @app.post("/api/v1/speech/stream", dependencies=guarded)
    async def stream_speech(request: SpeechSynthesisRequest) -> StreamingResponse:
        if speech_service is None:
            raise HTTPException(503, "Local speech is disabled. Run the voice setup first.")
        try:
            speech = await asyncio.to_thread(
                speech_service.synthesize, request.text, request.voice_id, request.rate
            )
        except SpeechBusyError as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "1"}) from exc
        except SpeechValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except SpeechUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc
        chunks = iter(speech.chunks)
        try:
            first = await asyncio.to_thread(_next_speech_chunk, chunks)
        except SpeechUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc
        if first is _STREAM_END:
            raise HTTPException(503, "Local speech synthesis returned no audio")
        audio_format = speech.voice.audio_format
        return StreamingResponse(
            _speech_chunk_bridge(chunks, cast(bytes, first)),
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Speech-Sample-Rate": str(audio_format.sample_rate),
                "X-Speech-Channels": str(audio_format.channels),
                "X-Speech-Sample-Width": str(audio_format.sample_width),
                "X-Speech-Encoding": audio_format.encoding,
                "X-Speech-Voice": speech.voice.voice_id,
                "X-Speech-Redacted": str(speech.redacted).lower(),
            },
        )

    def require_voice_conversations() -> VoiceConversationService:
        if voice_conversation_service is None:
            raise HTTPException(503, "Voice conversations are unavailable")
        return voice_conversation_service

    def require_voice_profiles() -> VoiceAgentProfileService:
        if voice_agent_profile_service is None:
            raise HTTPException(503, "Voice-agent profiles are unavailable")
        return voice_agent_profile_service

    def conversation_detail_dto(
        conversation: VoiceConversation, offset: int, limit: int
    ) -> dict[str, object]:
        value = _voice_conversation_detail(conversation, offset, limit)
        snapshot = conversation.agent_snapshot
        if snapshot is None:
            return value
        try:
            workspace_state = coordinator.state(snapshot.workspace_id)
            value["workspace_label"] = workspace_state.entry.label
            available_tools = {
                tool.definition.name for tool in workspace_state.runtime.registry.tools
            } | {"task_plan"}
            if snapshot.model not in workspace_state.runtime.settings.models or (
                set(snapshot.allowed_tools) - available_tools
            ):
                value["configuration_status"] = "unavailable"
            if workspace_state.active_task_id:
                value["active_task_state"] = coordinator.task(workspace_state.active_task_id).state
        except HarnessError:
            value["workspace_label"] = "Unavailable workspace"
            value["configuration_status"] = "unavailable"
        if (
            voice_agent_profile_service is not None
            and value.get("configuration_status") != "unavailable"
        ):
            try:
                current = voice_agent_profile_service.load(snapshot.profile_id)
                value["configuration_status"] = (
                    "outdated" if current.revision > snapshot.revision else "current"
                )
            except VoiceAgentProfileError:
                value["configuration_status"] = "detached"
        try:
            session = coordinator.state(snapshot.workspace_id).runtime.sessions.load(
                conversation.agent_session_id
            )
        except HarnessError:
            value["configuration_status"] = "unavailable"
            value["messages"] = []
            value["total_messages"] = 0
            return value
        messages = [item for item in session.messages if item.role in {"user", "assistant"}]
        page = messages[offset : offset + limit]
        value["messages"] = [
            {
                "message_id": f"{conversation.agent_session_id}-{offset + index}",
                "role": item.role,
                "content": item.content or "",
                "speech_text": (
                    markdown_to_speech_text(item.content or "") if item.role == "assistant" else ""
                ),
                "created_at": conversation.updated_at,
            }
            for index, item in enumerate(page)
        ]
        value["total_messages"] = len(messages)
        return value

    @app.get("/api/v1/speech/agent-profiles/catalog", dependencies=browser_guarded)
    async def voice_agent_profile_catalog() -> dict[str, object]:
        workspace_values: list[dict[str, object]] = []
        union: set[str] = set()
        plugin_union: set[str] = set()
        for entry in coordinator.workspaces():
            runtime = coordinator.state(entry.workspace_id).runtime
            plugin_names = {name for status in runtime.plugin_statuses for name in status.tools}
            plugin_union.update(plugin_names)
            descriptors = RequestToolRouter(runtime.registry.tools).catalog()
            tools = []
            for item in descriptors:
                if item.name == "discover_tools":
                    continue
                descriptor = asdict(item)
                if item.name in plugin_names:
                    descriptor["risk"] = "trusted"
                    descriptor["description"] = (
                        f"{item.description} Trusted in-process plugin code."
                    )[:240]
                descriptor["approval_required"] = item.risk == "approval"
                tools.append(descriptor)
            tools.append(
                {
                    "name": "task_plan",
                    "description": "Maintain a bounded observable task plan.",
                    "profile": "coding",
                    "risk": "read",
                    "keywords": ["task", "plan"],
                    "approval_required": False,
                }
            )
            union.update(str(item["name"]) for item in tools)
            workspace_values.append(
                {"workspace_id": entry.workspace_id, "label": entry.label, "tools": tools}
            )
        read_names = sorted(
            union
            & {
                "list_directory",
                "read_file",
                "search_text",
                "inspect_project",
                "find_code",
                "read_files",
                "git_inspect",
                "code_intelligence",
                "project_memory",
                "read_symbol",
                "changed_context",
                "dependency_context",
            }
        )
        return {
            "models": list(coordinator.settings.models),
            "voices": [asdict(item) for item in speech_service.voices()] if speech_service else [],
            "workspaces": workspace_values,
            "bounds": {
                "instructions": 4_000,
                "max_turns": [1, 100],
                "token_budget": [0, 1_000_000],
                "context_max_chars": [4_000, coordinator.settings.context_max_chars],
                "max_answer_chars": [500, 5_000],
                "tool_schema_limit": [1, 32],
                "speaking_rate": [0.75, 1.5],
            },
            "templates": [
                {
                    "template_id": "protected",
                    "name": "Protected Voice Chat",
                    "immutable": True,
                    "allowed_tools": [],
                },
                {"template_id": "reader", "name": "Workspace Reader", "allowed_tools": read_names},
                {
                    "template_id": "research",
                    "name": "Research",
                    "allowed_tools": sorted(set(read_names) | {"web_search", "read_web_pages"}),
                },
                {
                    "template_id": "coding",
                    "name": "Coding",
                    "allowed_tools": sorted(union - plugin_union),
                },
            ],
        }

    @app.get("/api/v1/speech/agent-profiles", dependencies=browser_guarded)
    async def voice_agent_profiles() -> list[dict[str, object]]:
        service = require_voice_profiles()
        return [
            _voice_agent_profile(item, service.unavailable_reasons(item))
            for item in await asyncio.to_thread(service.list_profiles)
        ]

    @app.post("/api/v1/speech/agent-profiles", dependencies=guarded)
    async def create_voice_agent_profile(value: VoiceAgentProfileRequest) -> dict[str, object]:
        service = require_voice_profiles()
        try:
            profile = await asyncio.to_thread(service.create, _voice_agent_spec(value))
            return _voice_agent_profile(profile, ())
        except VoiceAgentProfileError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/v1/speech/agent-profiles/{profile_id}", dependencies=browser_guarded)
    async def voice_agent_profile(profile_id: str) -> dict[str, object]:
        service = require_voice_profiles()
        try:
            profile = await asyncio.to_thread(service.load, profile_id)
            return _voice_agent_profile(profile, service.unavailable_reasons(profile))
        except VoiceAgentProfileError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.patch("/api/v1/speech/agent-profiles/{profile_id}", dependencies=guarded)
    async def update_voice_agent_profile(
        profile_id: str, value: VoiceAgentProfileRequest
    ) -> dict[str, object]:
        service = require_voice_profiles()
        try:
            profile = await asyncio.to_thread(service.update, profile_id, _voice_agent_spec(value))
            return _voice_agent_profile(profile, ())
        except VoiceAgentProfileError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/v1/speech/agent-profiles/{profile_id}/clone", dependencies=guarded)
    async def clone_voice_agent_profile(profile_id: str) -> dict[str, object]:
        service = require_voice_profiles()
        try:
            profile = await asyncio.to_thread(service.clone, profile_id)
            return _voice_agent_profile(profile, ())
        except VoiceAgentProfileError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/v1/speech/agent-profiles/{profile_id}", dependencies=guarded)
    async def delete_voice_agent_profile(
        profile_id: str, value: VoiceAgentProfileDelete
    ) -> dict[str, bool]:
        service = require_voice_profiles()
        try:
            await asyncio.to_thread(service.delete, profile_id, value.confirmation)
            return {"deleted": True}
        except VoiceAgentProfileError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/v1/speech/conversations", dependencies=browser_guarded)
    async def voice_conversations() -> list[dict[str, object]]:
        service = require_voice_conversations()
        try:
            conversations = await asyncio.to_thread(service.list_conversations)
        except VoiceConversationStorageError as exc:
            raise HTTPException(503, str(exc)) from exc
        summaries: list[dict[str, object]] = []
        for item in conversations:
            summary = _voice_conversation_summary(item)
            if item.agent_snapshot is not None:
                detail = conversation_detail_dto(item, 0, 1)
                for field in (
                    "configuration_status",
                    "workspace_label",
                    "active_task_state",
                ):
                    if field in detail:
                        summary[field] = detail[field]
            summaries.append(summary)
        return summaries

    @app.post("/api/v1/speech/conversations", dependencies=guarded)
    async def create_voice_conversation(value: VoiceConversationCreate) -> dict[str, object]:
        service = require_voice_conversations()
        try:
            if value.profile_id and value.profile_id != "protected":
                profiles = require_voice_profiles()
                profile = await asyncio.to_thread(profiles.load, value.profile_id)
                reasons = profiles.unavailable_reasons(profile)
                if reasons:
                    raise VoiceConversationValidationError("; ".join(reasons))
                state = coordinator.require_idle(profile.workspace_id)
                session = state.runtime.new_session()
                session.model = profile.model
                state.runtime.sessions.save(session)
                conversation = await asyncio.to_thread(
                    service.create_agent, profile.snapshot(), session.session_id
                )
            else:
                conversation = await asyncio.to_thread(service.create, value.model)
        except VoiceConversationBusyError as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "1"}) from exc
        except VoiceConversationValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoiceConversationStorageError as exc:
            raise HTTPException(503, str(exc)) from exc
        except VoiceAgentProfileError as exc:
            raise HTTPException(400, str(exc)) from exc
        return conversation_detail_dto(conversation, 0, 100)

    @app.get("/api/v1/speech/conversations/{conversation_id}", dependencies=browser_guarded)
    async def voice_conversation_detail(
        conversation_id: str,
        offset: Annotated[int, Query(ge=0, le=1_000)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> dict[str, object]:
        service = require_voice_conversations()
        try:
            conversation = await asyncio.to_thread(service.load, conversation_id)
        except VoiceConversationNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except VoiceConversationValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoiceConversationStorageError as exc:
            raise HTTPException(503, str(exc)) from exc
        return conversation_detail_dto(conversation, offset, limit)

    @app.patch("/api/v1/speech/conversations/{conversation_id}", dependencies=guarded)
    async def update_voice_conversation(
        conversation_id: str, value: VoiceConversationUpdate
    ) -> dict[str, object]:
        service = require_voice_conversations()
        try:
            conversation = await asyncio.to_thread(
                service.update, conversation_id, title=value.title, model=value.model
            )
        except VoiceConversationBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except VoiceConversationNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except VoiceConversationValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoiceConversationStorageError as exc:
            raise HTTPException(503, str(exc)) from exc
        return conversation_detail_dto(conversation, 0, 100)

    @app.delete("/api/v1/speech/conversations/{conversation_id}", dependencies=guarded)
    async def delete_voice_conversation(
        conversation_id: str, value: VoiceConversationDelete
    ) -> dict[str, bool]:
        service = require_voice_conversations()
        try:
            conversation = await asyncio.to_thread(service.load, conversation_id)
            await asyncio.to_thread(service.delete, conversation_id, value.confirmation)
            if conversation.agent_snapshot is not None:
                await asyncio.to_thread(
                    coordinator.state(
                        conversation.agent_snapshot.workspace_id
                    ).runtime.sessions.delete,
                    conversation.agent_session_id,
                )
        except VoiceConversationBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except VoiceConversationNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except VoiceConversationValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoiceConversationStorageError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"deleted": True}

    @app.post("/api/v1/speech/conversations/{conversation_id}/turns", dependencies=guarded)
    async def complete_voice_conversation_turn(
        conversation_id: str, value: VoiceConversationTurnRequest
    ) -> dict[str, object]:
        service = require_voice_conversations()
        try:
            turn = await asyncio.to_thread(service.complete_turn, conversation_id, value.text)
        except VoiceConversationBusyError as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "1"}) from exc
        except VoiceConversationNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except VoiceConversationValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoiceConversationStorageError as exc:
            raise HTTPException(503, str(exc)) from exc
        except ModelError as exc:
            raise HTTPException(503, "The configured model could not complete the reply") from exc
        return _voice_turn(turn)

    @app.post(
        "/api/v1/speech/conversations/{conversation_id}/profile-upgrade",
        dependencies=guarded,
    )
    async def upgrade_voice_agent_conversation(
        conversation_id: str, value: VoiceAgentProfileUpgrade
    ) -> dict[str, object]:
        service = require_voice_conversations()
        profiles = require_voice_profiles()
        try:
            conversation = await asyncio.to_thread(service.load, conversation_id)
            if conversation.agent_snapshot is None:
                raise VoiceConversationValidationError(
                    "Protected conversations cannot be upgraded in place"
                )
            coordinator.require_idle(conversation.agent_snapshot.workspace_id)
            profile = await asyncio.to_thread(profiles.load, value.profile_id)
            if profile.revision != value.revision:
                raise VoiceConversationValidationError("Profile revision is stale")
            reasons = profiles.unavailable_reasons(profile)
            if reasons:
                raise VoiceConversationValidationError("; ".join(reasons))
            updated = await asyncio.to_thread(
                service.upgrade_agent, conversation_id, profile.snapshot()
            )
            session = coordinator.state(profile.workspace_id).runtime.sessions.load(
                updated.agent_session_id
            )
            session.model = profile.model
            coordinator.state(profile.workspace_id).runtime.sessions.save(session)
            return conversation_detail_dto(updated, 0, 100)
        except HarnessError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post(
        "/api/v1/speech/conversations/{conversation_id}/agent-turns",
        dependencies=guarded,
        status_code=202,
    )
    async def complete_voice_agent_turn(
        conversation_id: str, value: VoiceAgentTurnRequest
    ) -> dict[str, object]:
        service = require_voice_conversations()
        try:
            conversation = await asyncio.to_thread(service.load, conversation_id)
            snapshot = conversation.agent_snapshot
            if snapshot is None:
                raise VoiceConversationValidationError("Conversation uses Protected Voice Chat")
            state = coordinator.state(snapshot.workspace_id)
            if snapshot.model not in state.runtime.settings.models:
                raise VoiceConversationValidationError("Snapshot model is unavailable")
            available = {tool.definition.name for tool in state.runtime.registry.tools} | {
                "task_plan"
            }
            missing = set(snapshot.allowed_tools) - available
            if missing:
                raise VoiceConversationValidationError(
                    "Snapshot tools are unavailable: " + ", ".join(sorted(missing))
                )
            session = state.runtime.sessions.load(conversation.agent_session_id)
            session.model = snapshot.model
            state.runtime.sessions.save(session)
            safe_text, _ = state.runtime.redactor.sanitize(value.text.strip())
            if not safe_text:
                raise VoiceConversationValidationError("Message cannot be empty")
            if conversation.title == "New conversation":
                await asyncio.to_thread(
                    service.update,
                    conversation_id,
                    title=" ".join(safe_text.split())[:60],
                )
            task = await coordinator.submit(
                snapshot.workspace_id,
                session.session_id,
                safe_text,
                value.client_id,
                policy=_voice_agent_execution_policy(snapshot),
            )
            return {
                "task": serialize_task(task),
                "conversation": _voice_conversation_summary(
                    await asyncio.to_thread(service.load, conversation_id)
                ),
            }
        except VoiceConversationBusyError as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "1"}) from exc
        except HarnessError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/v1/workspaces")
    async def workspaces() -> list[dict[str, object]]:
        return [
            {**asdict(item), "busy": coordinator.is_busy(item.workspace_id)}
            for item in coordinator.workspaces()
        ]

    @app.post("/api/v1/workspaces/validate", dependencies=guarded)
    async def validate_workspace(proposal: WorkspaceProposal) -> dict[str, object]:
        try:
            label, resolved = coordinator.catalog.validate(proposal.label, proposal.path)
        except HarnessError as exc:
            raise HTTPException(400, str(exc)) from exc
        challenge = new_session_id()
        challenges[challenge] = (label, resolved, time.monotonic() + 60)
        return {
            "challenge_id": challenge,
            "label": label,
            "resolved_path": str(resolved),
            "warning": "This workspace grants the harness read access and approved execution.",
            "expires_in_seconds": 60,
        }

    @app.post("/api/v1/workspaces/confirm", dependencies=guarded)
    async def confirm_workspace(value: WorkspaceConfirmation) -> dict[str, object]:
        challenge = challenges.pop(value.challenge_id, None)
        if challenge is None or challenge[2] < time.monotonic():
            raise HTTPException(409, "Workspace challenge is missing or expired")
        if not value.approved:
            raise HTTPException(409, "Workspace registration rejected")
        try:
            entry = coordinator.catalog.add(challenge[0], challenge[1])
        except HarnessError as exc:
            raise HTTPException(400, str(exc)) from exc
        return asdict(entry)

    @app.delete("/api/v1/workspaces/{workspace_id}", dependencies=guarded)
    async def remove_workspace(workspace_id: str) -> dict[str, object]:
        if coordinator.is_busy(workspace_id):
            raise HTTPException(409, "Workspace is busy")
        try:
            entry = coordinator.catalog.remove(workspace_id)
        except HarnessError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"removed": entry.workspace_id, "data_deleted": False}

    @app.get("/api/v1/workspaces/{workspace_id}/sessions")
    async def sessions(workspace_id: str) -> list[dict[str, object]]:
        try:
            values = coordinator.state(workspace_id).runtime.sessions.list_sessions()
        except HarnessError as exc:
            raise HTTPException(404, str(exc)) from exc
        return [_session_summary(item) for item in values]

    @app.post("/api/v1/workspaces/{workspace_id}/sessions", dependencies=guarded)
    async def new_session(workspace_id: str) -> dict[str, object]:
        try:
            state = coordinator.require_idle(workspace_id)
            return _session_detail(state.runtime.new_session())
        except HarnessError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/v1/workspaces/{workspace_id}/sessions/{session_id}")
    async def get_session(
        workspace_id: str, session_id: str, offset: int = 0, limit: int = 100
    ) -> dict[str, object]:
        try:
            session = coordinator.state(workspace_id).runtime.sessions.load(session_id)
        except HarnessError as exc:
            raise HTTPException(404, str(exc)) from exc
        return _session_detail(session, offset=max(0, offset), limit=min(max(1, limit), 100))

    @app.post(
        "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/requests",
        dependencies=guarded,
        status_code=202,
    )
    async def submit_prompt(
        workspace_id: str, session_id: str, value: PromptRequest
    ) -> dict[str, object]:
        try:
            state = coordinator.state(workspace_id)
            safe_prompt, redacted = state.runtime.redactor.sanitize(value.prompt)
            task = await coordinator.submit(
                workspace_id,
                session_id,
                safe_prompt,
                value.client_id,
                value.workflow_id,
            )
        except HarnessError as exc:
            raise HTTPException(409, str(exc)) from exc
        result = serialize_task(task)
        result.update({"display_prompt": safe_prompt, "redacted": redacted})
        return result

    @app.post(
        "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/commands",
        dependencies=guarded,
    )
    async def command(
        workspace_id: str, session_id: str, value: CommandRequest
    ) -> dict[str, object]:
        parsed = parse_command(value.value)
        if parsed.error or parsed.command is None:
            raise HTTPException(400, parsed.error or "A slash command is required")
        selected = parsed.command
        try:
            state = coordinator.state(workspace_id)
            session = state.runtime.sessions.load(session_id)
            if selected.name == "help":
                return {
                    "message": (
                        "Commands: /new /sessions /events /max-turns /model /models /resume "
                        "/session-info /summarize /quota /tag /tags /export "
                        "/archive /archives /restore /session-check /plugins /exit"
                        " /tools /plan /index /memory /workflows /workflow"
                        " /eval /handoff /candidate"
                    )
                }
            if selected.name == "new":
                return {
                    "session": _session_detail(
                        coordinator.require_idle(workspace_id).runtime.new_session()
                    )
                }
            if selected.name == "sessions":
                return {
                    "sessions": [
                        _session_summary(item) for item in state.runtime.sessions.list_sessions()
                    ]
                }
            if selected.name == "events":
                count = int(selected.argument) if selected.argument else 20
                return {"events": [asdict(item) for item in session.events[-count:]]}
            if selected.name == "resume":
                return {"session": _session_detail(state.runtime.sessions.load(selected.argument))}
            if selected.name == "session-info":
                target = (
                    state.runtime.sessions.load(selected.argument) if selected.argument else session
                )
                budget = target.token_budget_override or state.runtime.settings.session_token_budget
                return {"message": session_info(target, budget)}
            if selected.name == "max-turns":
                agent = coordinator.require_idle(workspace_id).runtime.agent(session)
                setting = selected.argument.casefold()
                if setting:
                    agent.configure_max_turns(None if setting == "reset" else int(setting))
                return {
                    "message": f"max LLM calls/request={agent.max_turns} ({agent.max_turns_source})"
                }
            if selected.name == "models":
                return {
                    "models": list(state.runtime.settings.models),
                    "current": session.model,
                }
            if selected.name == "model":
                if not selected.argument:
                    return {"model": session.model}
                agent = coordinator.require_idle(workspace_id).runtime.switch_model(
                    session,
                    None if selected.argument.casefold() == "reset" else selected.argument,
                )
                return {"model": agent.session.model}
            if selected.name == "quota":
                agent = coordinator.require_idle(workspace_id).runtime.agent(session)
                setting = selected.argument.casefold()
                if setting:
                    agent.configure_token_budget(None if setting == "reset" else int(setting))
                quota_display: int | str = agent.token_budget or "disabled"
                return {"message": (f"session tokens={agent.token_usage}, budget={quota_display}")}
            if selected.name == "tag":
                action, sequence, label = selected.argument.split(maxsplit=2)
                if action == "add":
                    state.runtime.session_service.add_tag(session, int(sequence), label)
                elif action == "remove":
                    state.runtime.session_service.remove_tag(session, int(sequence), label)
                else:
                    raise ValueError("Use add or remove")
                return {"message": f"Tag {action} completed"}
            if selected.name == "tags":
                tagged = state.runtime.session_service.tagged_events(session, selected.argument)
                return {"events": [asdict(item) for item in tagged]}
            if selected.name == "export":
                parts = selected.argument.split()
                target = state.runtime.sessions.load(parts[1]) if len(parts) > 1 else session
                return asdict(state.runtime.session_service.export(target, parts[0]))
            if selected.name == "archives":
                archives = state.runtime.session_service.list_archives()
                return {"archives": [asdict(item) for item in archives]}
            if selected.name == "restore":
                restored = coordinator.require_idle(workspace_id).runtime.session_service.restore(
                    selected.argument
                )
                return {"session": _session_detail(restored)}
            if selected.name == "plugins":
                return {"plugins": [asdict(item) for item in state.runtime.plugin_statuses]}
            if selected.name == "tools":
                agent = state.runtime.agent(session)
                return {"tools": [asdict(item) for item in agent.tool_catalog(selected.argument)]}
            if selected.name == "workflows":
                agent = state.runtime.agent(session)
                return {
                    "workflows": [
                        asdict(item) for item in agent.workflow_catalog(selected.argument)
                    ]
                }
            if selected.name == "workflow":
                agent = coordinator.require_idle(workspace_id).runtime.agent(session)
                parts = selected.argument.split()
                if parts == ["auto"]:
                    agent.configure_workflow(None)
                elif len(parts) == 2 and parts[0] == "use":
                    agent.configure_workflow(parts[1])
                elif parts not in ([], ["status"]):
                    raise ValueError("Usage: /workflow [status|auto|use <id>]")
                workflow_run = agent.workflow_status()
                return {
                    "pending_workflow_override": session.pending_workflow_override,
                    "workflow": asdict(workflow_run) if workflow_run is not None else None,
                }
            if selected.name == "eval":
                evaluation = state.runtime.evaluation
                if evaluation is None:
                    return {"message": "Evaluation is disabled."}
                parts = selected.argument.split()
                action = parts[0] if parts else "status"
                if action == "status":
                    return evaluation.status()
                if action == "contract":
                    number = int(parts[1]) if len(parts) > 1 else _latest_request_number(session)
                    contract = evaluation.contract(session.session_id, number)
                    return {"contract": asdict(contract) if contract else None}
                if action == "mark" and len(parts) >= 2:
                    observation = evaluation.mark(
                        session.session_id,
                        _latest_request_number(session),
                        cast(Literal["pass", "fail"], parts[1]),
                        " ".join(parts[2:]),
                    )
                    return {"observation": asdict(observation)}
                if action == "history":
                    limit = int(parts[1]) if len(parts) > 1 else 20
                    return {"observations": [asdict(item) for item in evaluation.history(limit)]}
                if action == "compare" and len(parts) == 3:
                    return {"comparison": asdict(evaluation.compare(parts[1], parts[2]))}
                if action == "run":
                    suite = next((item for item in parts[1:] if not item.startswith("--")), "core")
                    return {"run": asdict(evaluation.run_suite(suite, live="--live" in parts))}
                raise ValueError("Invalid /eval command")
            if selected.name == "handoff":
                evaluation = state.runtime.evaluation
                handoff = evaluation.handoff(session.session_id) if evaluation else None
                return {"handoff": asdict(handoff) if handoff else None}
            if selected.name == "candidate":
                evaluation = state.runtime.evaluation
                if evaluation is None:
                    return {"message": "Evaluation is disabled."}
                parts = selected.argument.split(maxsplit=2)
                if parts and parts[0] == "propose":
                    candidate = cast(
                        HarnessCandidate,
                        await coordinator.run_auxiliary(
                            workspace_id,
                            session_id,
                            value.client_id,
                            "candidate-proposal",
                            lambda: evaluation.propose(
                                state.runtime.model_client_for(session.model),
                                parts[1] if len(parts) > 1 else "",
                            ),
                        ),
                    )
                elif len(parts) == 2 and parts[0] == "show":
                    candidate = evaluation.candidate(parts[1])
                elif len(parts) == 2 and parts[0] == "approve":
                    candidate = evaluation.decide_candidate(parts[1], True)
                elif len(parts) >= 2 and parts[0] == "reject":
                    candidate = evaluation.decide_candidate(
                        parts[1], False, parts[2] if len(parts) > 2 else ""
                    )
                else:
                    raise ValueError("Invalid /candidate command")
                return {"candidate": asdict(candidate)}
            if selected.name == "plan":
                plan = session.plans[-1] if session.plans else None
                return {"plan": asdict(plan) if plan is not None else None}
            if selected.name == "index":
                if selected.argument not in {"", "refresh", "rebuild"}:
                    raise ValueError("Usage: /index [refresh|rebuild]")
                agent = state.runtime.agent(session)
                if not selected.argument:
                    status = agent.project_index_status()
                else:
                    status = cast(
                        ProjectIndexStatus,
                        await coordinator.run_auxiliary(
                            workspace_id,
                            session_id,
                            value.client_id,
                            "project-index",
                            lambda: agent.refresh_project_index(
                                rebuild=selected.argument == "rebuild"
                            ),
                        ),
                    )
                return {"project_memory": asdict(status) if status is not None else None}
            if selected.name == "memory":
                agent = state.runtime.agent(session)
                memory_result = cast(
                    RetrievedProjectContext,
                    await coordinator.run_auxiliary(
                        workspace_id,
                        session_id,
                        value.client_id,
                        "project-memory",
                        lambda: agent.query_project_memory(selected.argument),
                    ),
                )
                return {"project_memory": asdict(memory_result)}
            if selected.name == "summarize":
                target = (
                    state.runtime.sessions.load(selected.argument) if selected.argument else session
                )
                agent = state.runtime.agent(target)
                summary = await coordinator.run_auxiliary(
                    workspace_id,
                    target.session_id,
                    value.client_id,
                    "summarize",
                    agent.summarize_with_model,
                )
                return {"message": str(summary)}
            if selected.name == "archive":
                archive_result = await coordinator.run_auxiliary(
                    workspace_id,
                    selected.argument,
                    value.client_id,
                    "archive",
                    lambda: state.runtime.session_service.archive(selected.argument),
                )
                if not isinstance(archive_result, ArchiveInfo):
                    raise SessionError("Archive returned an invalid result")
                return asdict(archive_result)
            if selected.name == "session-check":
                parts = selected.argument.split()
                if not parts:
                    findings = state.runtime.session_service.scan()
                    return {"findings": [asdict(item) for item in findings]}
                if len(parts) == 2 and parts[0] == "quarantine":
                    quarantine_result = await coordinator.run_auxiliary(
                        workspace_id,
                        session_id,
                        value.client_id,
                        "quarantine",
                        lambda: state.runtime.session_service.quarantine(parts[1]),
                    )
                    return {"message": f"Quarantined: {quarantine_result}"}
                raise ValueError("Usage: /session-check [quarantine <check-id>]")
            if selected.name == "exit":
                return {"message": "Session saved. Close this browser tab when ready."}
            raise ValueError("Unsupported browser command")
        except (HarnessError, ValueError, IndexError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/v1/tasks/{task_id}")
    async def task(task_id: str) -> dict[str, object]:
        try:
            return serialize_task(coordinator.task(task_id))
        except HarnessError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/v1/tasks/{task_id}/cancel", dependencies=guarded)
    async def cancel_task(task_id: str, value: TaskCancellationRequest) -> dict[str, object]:
        try:
            return serialize_task(await coordinator.cancel(task_id, value.client_id))
        except HarnessError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/v1/approvals/{approval_id}", dependencies=guarded)
    async def resolve_approval(approval_id: str, value: ApprovalResolution) -> dict[str, object]:
        if not coordinator.resolve_approval(
            value.workspace_id,
            approval_id,
            value.client_id,
            value.approved,
            value.feedback,
        ):
            raise HTTPException(403, "Approval is missing or belongs to another browser")
        return {"resolved": True, "approved": value.approved}

    @app.get("/api/v1/workspaces/{workspace_id}/sessions/{session_id}/events")
    async def events(
        workspace_id: str, session_id: str, filter: str = "", count: int = 20
    ) -> list[dict[str, object]]:
        try:
            state = coordinator.state(workspace_id)
            session = state.runtime.sessions.load(session_id)
            values = state.runtime.session_service.filter_events(session, filter)
            return [asdict(item) for item in values[-min(max(count, 1), 500) :]]
        except HarnessError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post(
        "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/events/{sequence}/tags",
        dependencies=guarded,
    )
    async def add_tag(
        workspace_id: str, session_id: str, sequence: int, value: TagUpdate
    ) -> dict[str, object]:
        try:
            state = coordinator.require_idle(workspace_id)
            session = state.runtime.sessions.load(session_id)
            tag = state.runtime.session_service.add_tag(session, sequence, value.label)
            return {"tag": tag}
        except HarnessError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.delete(
        "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/events/{sequence}/tags/{label}",
        dependencies=guarded,
    )
    async def remove_tag(
        workspace_id: str, session_id: str, sequence: int, label: str
    ) -> dict[str, object]:
        try:
            state = coordinator.require_idle(workspace_id)
            session = state.runtime.sessions.load(session_id)
            tag = state.runtime.session_service.remove_tag(session, sequence, label)
            return {"tag": tag}
        except HarnessError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.put(
        "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/max-turns",
        dependencies=guarded,
    )
    async def max_turns(
        workspace_id: str, session_id: str, value: LimitUpdate
    ) -> dict[str, object]:
        try:
            state = coordinator.require_idle(workspace_id)
            agent = state.runtime.agent(state.runtime.sessions.load(session_id))
            return {
                "value": agent.configure_max_turns(value.value),
                "source": agent.max_turns_source,
            }
        except (HarnessError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.put(
        "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/model",
        dependencies=guarded,
    )
    async def select_model(
        workspace_id: str, session_id: str, value: ModelUpdate
    ) -> dict[str, object]:
        """Persist a configured model selection between requests."""
        try:
            state = coordinator.require_idle(workspace_id)
            session = state.runtime.sessions.load(session_id)
            agent = state.runtime.switch_model(session, value.model)
            return {"model": agent.session.model, "models": list(state.runtime.settings.models)}
        except HarnessError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.put(
        "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/quota",
        dependencies=guarded,
    )
    async def quota(workspace_id: str, session_id: str, value: LimitUpdate) -> dict[str, object]:
        try:
            state = coordinator.require_idle(workspace_id)
            agent = state.runtime.agent(state.runtime.sessions.load(session_id))
            return {"value": agent.configure_token_budget(value.value), "usage": agent.token_usage}
        except (HarnessError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post(
        "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/summarize",
        dependencies=guarded,
    )
    async def summarize(
        workspace_id: str, session_id: str, value: AuxiliaryRequest
    ) -> dict[str, object]:
        state = coordinator.require_idle(workspace_id)
        agent = state.runtime.agent(state.runtime.sessions.load(session_id))
        try:
            result = await coordinator.run_auxiliary(
                workspace_id, session_id, value.client_id, "summarize", agent.summarize_with_model
            )
            return {"summary": str(result)}
        except HarnessError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post(
        "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/export",
        dependencies=guarded,
    )
    async def export_session(
        workspace_id: str, session_id: str, value: ExportRequest
    ) -> dict[str, object]:
        try:
            state = coordinator.require_idle(workspace_id)
            session = state.runtime.sessions.load(session_id)
            return asdict(state.runtime.session_service.export(session, value.format))
        except HarnessError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/v1/workspaces/{workspace_id}/archives")
    async def archives(workspace_id: str) -> list[dict[str, object]]:
        try:
            return [
                asdict(item)
                for item in coordinator.state(workspace_id).runtime.session_service.list_archives()
            ]
        except HarnessError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post(
        "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/archive",
        dependencies=guarded,
    )
    async def archive_session(
        workspace_id: str, session_id: str, value: AuxiliaryRequest
    ) -> dict[str, object]:
        state = coordinator.require_idle(workspace_id)
        try:
            result = await coordinator.run_auxiliary(
                workspace_id,
                session_id,
                value.client_id,
                "archive",
                lambda: state.runtime.session_service.archive(session_id),
            )
            if not isinstance(result, ArchiveInfo):
                raise HTTPException(500, "Archive returned an invalid result")
            return asdict(result)
        except HarnessError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post(
        "/api/v1/workspaces/{workspace_id}/archives/{session_id}/restore",
        dependencies=guarded,
    )
    async def restore_session(workspace_id: str, session_id: str) -> dict[str, object]:
        try:
            state = coordinator.require_idle(workspace_id)
            return _session_detail(state.runtime.session_service.restore(session_id))
        except HarnessError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/v1/workspaces/{workspace_id}/plugins")
    async def plugins(workspace_id: str) -> list[dict[str, object]]:
        return [asdict(item) for item in coordinator.state(workspace_id).runtime.plugin_statuses]

    @app.get("/api/v1/workspaces/{workspace_id}/project-memory")
    async def project_memory_status(workspace_id: str) -> dict[str, object] | None:
        """Return current workspace-memory status without triggering indexing."""
        state = coordinator.state(workspace_id)
        status = state.runtime.project_memory.status() if state.runtime.project_memory else None
        return asdict(status) if status is not None else None

    @app.get("/api/v1/workspaces/{workspace_id}/workflows")
    async def workflow_catalog(workspace_id: str, query: str = "") -> list[dict[str, object]]:
        """Return the shared built-in workflow catalog."""
        coordinator.state(workspace_id)
        return [asdict(item) for item in WorkflowCatalog().list(query)]

    @app.get("/api/v1/workspaces/{workspace_id}/sessions/{session_id}/workflow")
    async def workflow_status(
        workspace_id: str,
        session_id: str,
        request_number: Annotated[int | None, Query(ge=1)] = None,
    ) -> dict[str, object]:
        """Return the pending override and latest workflow state."""
        state = coordinator.state(workspace_id)
        session = state.runtime.sessions.load(session_id)
        run = next(
            (
                item
                for item in reversed(session.workflows)
                if request_number is None or item.request_number == request_number
            ),
            None,
        )
        return {
            "pending_workflow_override": session.pending_workflow_override,
            "workflow": asdict(run) if run is not None else None,
        }

    @app.put(
        "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/workflow",
        dependencies=guarded,
    )
    async def set_workflow_override(
        workspace_id: str, session_id: str, value: WorkflowOverride
    ) -> dict[str, object]:
        """Set or clear the persisted one-shot workflow override."""
        state = coordinator.require_idle(workspace_id)
        session = state.runtime.sessions.load(session_id)
        agent = state.runtime.agent(session)
        agent.configure_workflow(value.workflow_id)
        return {"pending_workflow_override": value.workflow_id}

    @app.get("/api/v1/workspaces/{workspace_id}/evaluations/status")
    async def evaluation_status(workspace_id: str) -> dict[str, object]:
        """Return aggregate evaluation quality and efficiency metrics."""
        evaluation = coordinator.state(workspace_id).runtime.evaluation
        return evaluation.status() if evaluation is not None else {"enabled": False}

    @app.get("/api/v1/workspaces/{workspace_id}/evaluations/history")
    async def evaluation_history(
        workspace_id: str, limit: Annotated[int, Query(ge=1, le=1_000)] = 20
    ) -> list[dict[str, object]]:
        """Return recent redacted evaluation observations."""
        evaluation = coordinator.state(workspace_id).runtime.evaluation
        return [asdict(item) for item in evaluation.history(limit)] if evaluation else []

    @app.get("/api/v1/workspaces/{workspace_id}/sessions/{session_id}/evaluations/{request_number}")
    async def evaluation_contract(
        workspace_id: str, session_id: str, request_number: int
    ) -> dict[str, object]:
        """Return one request contract, observation, and latest handoff."""
        evaluation = coordinator.state(workspace_id).runtime.evaluation
        if evaluation is None:
            return {"contract": None, "observation": None, "handoff": None}
        contract = evaluation.contract(session_id, request_number)
        observation = next(
            (
                item
                for item in evaluation.history(1_000)
                if item.session_id == session_id and item.request_number == request_number
            ),
            None,
        )
        handoff = evaluation.handoff(session_id)
        return {
            "contract": asdict(contract) if contract else None,
            "observation": asdict(observation) if observation else None,
            "handoff": asdict(handoff) if handoff else None,
        }

    @app.put(
        "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/evaluations/mark",
        dependencies=guarded,
    )
    async def mark_evaluation(
        workspace_id: str, session_id: str, value: EvaluationMark
    ) -> dict[str, object]:
        """Attach an explicit pass/fail mark to one deterministic observation."""
        evaluation = coordinator.require_idle(workspace_id).runtime.evaluation
        if evaluation is None:
            raise HTTPException(409, "Evaluation is disabled")
        return {
            "observation": asdict(
                evaluation.mark(session_id, value.request_number, value.outcome, value.note)
            )
        }

    @app.get("/api/v1/workspaces/{workspace_id}/candidates")
    async def candidate_list(workspace_id: str) -> list[dict[str, object]]:
        """Return recent controlled proposals without applying them."""
        evaluation = coordinator.state(workspace_id).runtime.evaluation
        if evaluation is None:
            return []
        return [asdict(item) for item in evaluation.candidates(20)]

    @app.post(
        "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/candidates",
        dependencies=guarded,
    )
    async def propose_candidate(
        workspace_id: str, session_id: str, value: CandidateProposalRequest
    ) -> dict[str, object]:
        """Run one explicit bounded proposal call away from the event loop."""
        state = coordinator.require_idle(workspace_id)
        evaluation = state.runtime.evaluation
        if evaluation is None:
            raise HTTPException(409, "Evaluation is disabled")
        candidate = cast(
            HarnessCandidate,
            await coordinator.run_auxiliary(
                workspace_id,
                session_id,
                value.client_id,
                "candidate-proposal",
                lambda: evaluation.propose(
                    state.runtime.model_client_for(state.runtime.sessions.load(session_id).model),
                    value.component_id,
                ),
            ),
        )
        return {"candidate": asdict(candidate)}

    @app.put(
        "/api/v1/workspaces/{workspace_id}/candidates/{candidate_id}",
        dependencies=guarded,
    )
    async def decide_candidate(
        workspace_id: str, candidate_id: str, value: CandidateDecision
    ) -> dict[str, object]:
        """Approve or reject a proposal without modifying source files."""
        evaluation = coordinator.require_idle(workspace_id).runtime.evaluation
        if evaluation is None:
            raise HTTPException(409, "Evaluation is disabled")
        return {
            "candidate": asdict(
                evaluation.decide_candidate(candidate_id, value.approved, value.feedback)
            )
        }

    @app.get("/api/v1/workspaces/{workspace_id}/integrity")
    async def integrity(workspace_id: str) -> list[dict[str, object]]:
        return [
            asdict(item) for item in coordinator.state(workspace_id).runtime.session_service.scan()
        ]

    @app.websocket("/api/v1/stream")
    async def stream(
        websocket: WebSocket,
        client_id: Annotated[str, Query(min_length=16, max_length=128)],
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> None:
        browser = websocket.cookies.get(_COOKIE)
        origin = websocket.headers.get("origin")
        if browser not in security.sessions or origin not in security.origins:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = await coordinator.hub.subscribe(client_id, after)
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(WebEventHub.serialize(event))
                if event.type == "resync_required":
                    await websocket.close(code=1013)
                    return
        except WebSocketDisconnect:
            pass
        finally:
            await coordinator.hub.unsubscribe(client_id)
            coordinator.disconnect(client_id)

    @app.websocket("/api/v1/speech/input/stream")
    async def speech_input_stream(websocket: WebSocket) -> None:
        """Accept one authenticated, bounded, local-only microphone PCM stream."""
        browser = websocket.cookies.get(_COOKIE)
        origin = websocket.headers.get("origin")
        if browser not in security.sessions or origin not in security.origins:
            await websocket.close(code=1008)
            return
        if speech_input_service is None:
            await websocket.close(code=1013)
            return
        await websocket.accept()
        session: SpeechInputSession | None = None
        try:
            first = await asyncio.wait_for(websocket.receive_text(), timeout=5)
            start = SpeechInputStart.model_validate(json.loads(first))
            security.validate(browser, start.csrf_token, origin)
            try:
                session = await asyncio.to_thread(speech_input_service.open_session, start.mode)
            except SpeechInputBusyError:
                await websocket.send_json(
                    {"type": "busy", "reason": "Another microphone session is active"}
                )
                await websocket.close(code=1013)
                return
            await websocket.send_json({"type": "ready", "reason": start.mode})
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                chunk = message.get("bytes")
                if chunk is not None:
                    events = await asyncio.to_thread(session.accept, chunk)
                    for event in events:
                        await websocket.send_json(asdict(event))
                        if event.type == "transcribing":
                            result = await asyncio.to_thread(session.transcribe_pending)
                            await websocket.send_json(asdict(result))
                    continue
                raw_text = message.get("text")
                if raw_text is None:
                    raise SpeechInputValidationError("Invalid microphone WebSocket frame")
                control = SpeechInputControl.model_validate(json.loads(raw_text))
                if control.type == "close":
                    await websocket.close(code=1000)
                    return
                action = {
                    "begin_tap": session.begin_tap,
                    "finish": session.finish,
                    "pause": session.pause,
                    "rearm": session.rearm,
                    "cancel": session.cancel,
                }[control.type]
                events = await asyncio.to_thread(action)
                for event in events:
                    await websocket.send_json(asdict(event))
                    if event.type == "transcribing":
                        result = await asyncio.to_thread(session.transcribe_pending)
                        await websocket.send_json(asdict(result))
        except (
            HTTPException,
            json.JSONDecodeError,
            RuntimeError,
            ValidationError,
            SpeechInputValidationError,
        ):
            await websocket.send_json({"type": "error", "reason": "Invalid microphone stream"})
            await websocket.close(code=1008)
        except SpeechInputUnavailableError:
            await websocket.send_json(
                {"type": "error", "reason": "Local speech recognition failed"}
            )
            await websocket.close(code=1011)
        except (TimeoutError, WebSocketDisconnect):
            return
        finally:
            if session is not None:
                await asyncio.to_thread(session.close)

    if static_directory.is_dir():
        assets = static_directory / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/error", include_in_schema=False)
        async def stale_legacy_error() -> RedirectResponse:
            """Redirect a cached legacy Open WebUI error location to the harness root."""
            return RedirectResponse(
                url="/",
                status_code=307,
                headers={
                    "Cache-Control": "no-store",
                    "Clear-Site-Data": '"cache", "storage"',
                },
            )

        @app.get("/api/config", include_in_schema=False)
        @app.get("/manifest.json", include_in_schema=False)
        @app.get("/service-worker.js", include_in_schema=False)
        @app.get("/sw.js", include_in_schema=False)
        async def stale_legacy_resource() -> Response:
            """Expire browser state left by the retired Open WebUI origin."""
            return Response(
                '{"detail":"Legacy UI cache cleared; reload the Harness GUI."}',
                status_code=410,
                media_type="application/json",
                headers={
                    "Cache-Control": "no-store",
                    "Clear-Site-Data": '"cache", "storage"',
                },
            )

        @app.get("/_app/{legacy_path:path}", include_in_schema=False)
        @app.get("/static/{legacy_path:path}", include_in_schema=False)
        async def stale_legacy_asset(legacy_path: str) -> Response:
            """Reject obsolete asset requests and clear their registered service worker."""
            del legacy_path
            return await stale_legacy_resource()

        @app.get("/{path:path}")
        async def spa(path: str) -> FileResponse:
            candidate = static_directory / path
            if path and candidate.is_file() and static_directory in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(
                static_directory / "index.html", headers={"Cache-Control": "no-store"}
            )

    return app


def _session_summary(session: Session) -> dict[str, object]:
    """Return bounded session picker metadata."""
    preview = next(
        (message.content or "" for message in reversed(session.messages) if message.role == "user"),
        "",
    )
    return {
        "session_id": session.session_id,
        "model": session.model,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "preview": preview[:160],
        "summary": session.summary.text if session.summary else "",
        "max_turns_override": session.max_turns_override,
        "token_budget_override": session.token_budget_override,
    }


def _voice_conversation_summary(conversation: VoiceConversation) -> dict[str, object]:
    """Return bounded picker metadata without transcript content."""
    snapshot = conversation.agent_snapshot
    return {
        "conversation_id": conversation.conversation_id,
        "title": conversation.title,
        "model": conversation.model,
        "message_count": len(conversation.messages),
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "mode": "agent" if snapshot else "protected",
        "profile_id": snapshot.profile_id if snapshot else "protected",
        "profile_revision": snapshot.revision if snapshot else 1,
        "profile_name": snapshot.name if snapshot else "Protected Voice Chat",
        "workspace_id": snapshot.workspace_id if snapshot else None,
        "configuration_status": "current",
        "auto_speak": snapshot.auto_speak if snapshot else True,
        "voice_id": snapshot.voice_id if snapshot else "",
        "speaking_rate": snapshot.speaking_rate if snapshot else 1.0,
    }


def _voice_message(message: VoiceConversationMessage) -> dict[str, object]:
    """Return one display message and derived non-persisted speech text."""
    return {
        "message_id": message.message_id,
        "role": message.role,
        "content": message.content,
        "speech_text": (
            markdown_to_speech_text(message.content) if message.role == "assistant" else ""
        ),
        "created_at": message.created_at,
    }


def _voice_conversation_detail(
    conversation: VoiceConversation, offset: int, limit: int
) -> dict[str, object]:
    """Return one newest-first bounded transcript page in chronological order."""
    end = len(conversation.messages) - offset
    start = max(0, end - limit)
    page = conversation.messages[start : max(0, end)] if end > 0 else []
    return {
        **_voice_conversation_summary(conversation),
        "messages": [_voice_message(item) for item in page],
        "has_older_messages": start > 0,
        "total_messages": len(conversation.messages),
        "snapshot": asdict(conversation.agent_snapshot) if conversation.agent_snapshot else None,
    }


def _voice_agent_spec(value: VoiceAgentProfileRequest) -> VoiceAgentProfileSpec:
    """Project the closed browser model into an application value."""
    return VoiceAgentProfileSpec(
        value.name,
        value.instructions,
        value.workspace_id,
        value.model,
        tuple(value.allowed_tools),
        value.project_context_enabled,
        value.workflow_mode,
        value.max_turns,
        value.token_budget,
        value.context_max_chars,
        value.max_answer_chars,
        value.tool_schema_limit,
        value.tool_activation_limit,
        value.voice_id,
        value.speaking_rate,
        value.auto_speak,
    )


def _voice_agent_profile(
    profile: VoiceAgentProfile, unavailable_reasons: tuple[str, ...]
) -> dict[str, object]:
    """Return a bounded profile DTO with dependency availability."""
    value = asdict(profile)
    value["allowed_tools"] = list(profile.allowed_tools)
    value["available"] = not unavailable_reasons
    value["unavailable_reasons"] = list(unavailable_reasons)
    return value


def _voice_agent_execution_policy(snapshot: VoiceAgentSnapshot) -> VoiceAgentExecutionPolicy:
    """Project a persisted snapshot into the generic runtime policy."""
    return VoiceAgentExecutionPolicy(
        snapshot.instructions,
        snapshot.allowed_tools,
        snapshot.project_context_enabled,
        snapshot.workflow_mode,
        snapshot.max_turns,
        snapshot.token_budget,
        snapshot.context_max_chars,
        snapshot.max_answer_chars,
        snapshot.tool_schema_limit,
        snapshot.tool_activation_limit,
    )


def _voice_turn(turn: VoiceConversationTurn) -> dict[str, object]:
    """Return one completed atomic turn with safe provider usage metadata."""
    return {
        "conversation": _voice_conversation_summary(turn.conversation),
        "user_message": _voice_message(turn.user_message),
        "assistant_message": _voice_message(turn.assistant_message),
        "speech_text": turn.speech_text,
        "redacted": turn.redacted,
        "usage": {
            "input_tokens": turn.input_tokens,
            "output_tokens": turn.output_tokens,
        },
    }


def _latest_request_number(session: Session) -> int:
    """Return the latest persisted request number, defaulting to one."""
    values = [item.request_number for item in session.messages if item.request_number is not None]
    values.extend(item.request_number for item in session.events if item.request_number is not None)
    return max(values, default=1)


def _session_detail(session: Session, *, offset: int = 0, limit: int = 100) -> dict[str, object]:
    """Return one browser-safe session transcript page and analytics."""
    visible = [
        item
        for item in session.messages
        if item.role in {"user", "assistant"}
        and item.content
        and not (item.role == "assistant" and item.tool_calls)
    ]
    page = visible[max(0, len(visible) - offset - limit) : len(visible) - offset or None]
    return {
        **_session_summary(session),
        "workspace": session.workspace,
        "messages": [
            {
                **asdict(item),
                "content": (
                    normalize_assistant_markdown(item.content or "")
                    if item.role == "assistant"
                    else item.content
                ),
            }
            for item in page
        ],
        "events": [asdict(item) for item in session.events],
        "plans": [asdict(item) for item in session.plans],
        "evidence": [asdict(item) for item in session.evidence],
        "workflows": [asdict(item) for item in session.workflows],
        "pending_workflow_override": session.pending_workflow_override,
        "has_older_messages": len(visible) > offset + limit,
        "info": session_info(session, session.token_budget_override or 0),
    }
