"""Offline tests for revisioned configurable voice-agent profiles."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from local_harness.application.tool_registry import ToolRegistry
from local_harness.application.voice_agent_profiles import VoiceAgentProfileService
from local_harness.application.voice_conversation import VoiceConversationService
from local_harness.domain.errors import (
    ToolExecutionError,
    VoiceAgentProfileNotFoundError,
    VoiceAgentProfileStorageError,
    VoiceAgentProfileValidationError,
)
from local_harness.domain.models import Message, ToolDefinition, ToolResult
from local_harness.domain.voice_agent import VoiceAgentProfileSpec
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.voice_agent_profiles import JsonVoiceAgentProfileRepository
from local_harness.infrastructure.voice_conversations import JsonVoiceConversationRepository
from local_harness.interfaces.web.api import create_app
from local_harness.interfaces.web.coordinator import WebRuntimeCoordinator


class NamedTool:
    """Minimal deterministic tool for exact registry filtering."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def definition(self) -> ToolDefinition:
        """Return one closed no-argument schema."""
        return ToolDefinition(
            self._name,
            self._name,
            {"type": "object", "properties": {}, "additionalProperties": False},
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Return a bounded observation."""
        return ToolResult("ok")


def _service(tmp_path: Path) -> VoiceAgentProfileService:
    redactor = SecretRedactor(("sk-secret-profile-value",))
    return VoiceAgentProfileService(
        JsonVoiceAgentProfileRepository(tmp_path, redactor),
        redactor.sanitize,
        workspace_ids=lambda: {"workspace-a"},
        tool_names=lambda _workspace: {"read_file", "apply_patch"},
        models=("model-a",),
        voices=("voice-a",),
        global_context_max_chars=30_000,
    )


def _spec() -> VoiceAgentProfileSpec:
    return VoiceAgentProfileSpec(
        "Reader",
        "Never reveal sk-secret-profile-value",
        "workspace-a",
        "model-a",
        ("read_file",),
        True,
        "off",
        8,
        0,
        30_000,
        1_500,
        8,
        5,
        "voice-a",
        1.0,
        True,
    )


def test_profile_is_redacted_revisioned_snapshotted_cloned_and_deleted(tmp_path: Path) -> None:
    """Persist sanitized values and keep prior snapshots immutable."""
    service = _service(tmp_path)
    created = service.create(_spec())
    snapshot = created.snapshot()
    assert "sk-secret-profile-value" not in created.instructions
    updated = service.update(created.profile_id, replace(_spec(), name="Reader 2"))
    assert updated.revision == 2
    assert snapshot.name == "Reader"
    assert service.clone(created.profile_id).name == "Reader 2 copy"
    with pytest.raises(VoiceAgentProfileValidationError):
        service.delete(created.profile_id, "wrong")
    service.delete(created.profile_id, created.profile_id)
    assert all(item.profile_id != created.profile_id for item in service.list_profiles())


@pytest.mark.parametrize(
    "change",
    [
        {"workspace_id": "missing"},
        {"model": "missing"},
        {"allowed_tools": ("unknown",)},
        {"allowed_tools": ("read_file", "read_file")},
        {"name": ""},
        {"instructions": "x" * 4_001},
        {"workflow_mode": "invalid"},
        {"max_turns": 101},
        {"token_budget": 1_000_001},
        {"context_max_chars": 3_999},
        {"max_answer_chars": 499},
        {"tool_schema_limit": 33},
        {"tool_activation_limit": 9},
        {"voice_id": "missing"},
        {"speaking_rate": 1.51},
    ],
)
def test_profile_rejects_values_outside_exact_bounds(
    tmp_path: Path, change: dict[str, object]
) -> None:
    """Reject missing dependencies and every configured execution bound."""
    with pytest.raises(VoiceAgentProfileValidationError):
        _service(tmp_path).create(replace(_spec(), **cast(Any, change)))


def test_registry_filter_never_exposes_or_executes_disallowed_tools() -> None:
    """Construct a registry containing only exact profile-selected names."""
    allowed = NamedTool("read_file")
    denied = NamedTool("apply_patch")
    restricted = ToolRegistry([allowed, denied]).restricted_to(("read_file",))
    assert [item.definition.name for item in restricted.tools] == ["read_file"]
    with pytest.raises(ToolExecutionError):
        restricted.get("apply_patch")


def test_repository_and_dependency_failures_are_bounded(tmp_path: Path) -> None:
    """Translate missing/corrupt documents and report every missing exact dependency."""
    service = _service(tmp_path)
    created = service.create(_spec())
    repository = JsonVoiceAgentProfileRepository(
        tmp_path, SecretRedactor(("sk-secret-profile-value",))
    )
    with pytest.raises(VoiceAgentProfileValidationError):
        repository.load("unsafe")
    with pytest.raises(VoiceAgentProfileNotFoundError):
        repository.load("f" * 32)
    (tmp_path / ".harness" / "voice-agent-profiles" / f"{created.profile_id}.json").write_text(
        "{}", encoding="utf-8"
    )
    with pytest.raises(VoiceAgentProfileStorageError):
        repository.load(created.profile_id)
    assert repository.list_profiles() == []
    unavailable = VoiceAgentProfileService(
        repository,
        lambda value: (value, False),
        workspace_ids=lambda: {"workspace-a"},
        tool_names=lambda _workspace: set(),
        models=(),
        voices=("voice-a",),
        global_context_max_chars=30_000,
    )
    reasons = unavailable.unavailable_reasons(created)
    assert "Model is no longer configured" in reasons
    assert any("read_file" in reason for reason in reasons)
    missing_workspace = replace(created, workspace_id="missing")
    assert unavailable.unavailable_reasons(missing_workspace) == (
        "Workspace is no longer registered",
    )


class DirectModel:
    """Return a deterministic protected-conversation answer."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> Message:
        """Return one answer without external access."""
        self.calls += 1
        return Message(
            "assistant", "<step_summary>Answered safely</step_summary>\n## Result\n\nDone."
        )


def test_profile_api_crud_catalog_snapshots_and_protected_compatibility(tmp_path: Path) -> None:
    """Protect mutations, snapshot access, and reject the legacy turn path for agents."""
    tmp_path.joinpath(".env").write_text(
        "OPENAI_API_KEY=sk-local-real-test-key\n"
        "OPENAI_BASE_URL=http://127.0.0.1:4000/v1\n"
        "OPENAI_MODEL=model-a\n"
        "HARNESS_MODELS=model-a\n",
        encoding="utf-8",
    )
    static = tmp_path / "static"
    static.mkdir()
    static.joinpath("index.html").write_text("<main>Harness</main>", encoding="utf-8")
    coordinator = WebRuntimeCoordinator(tmp_path, tmp_path / "catalog.json")
    workspace_id = coordinator.workspaces()[0].workspace_id
    redactor = SecretRedactor(("sk-local-real-test-key",))
    profiles = VoiceAgentProfileService(
        JsonVoiceAgentProfileRepository(tmp_path, redactor),
        redactor.sanitize,
        workspace_ids=lambda: {workspace_id},
        tool_names=lambda _workspace: {"read_file"},
        models=("model-a",),
        voices=("voice-a",),
        global_context_max_chars=60_000,
    )
    protected_model = DirectModel()
    conversations = VoiceConversationService(
        JsonVoiceConversationRepository(tmp_path, redactor),
        {"model-a": protected_model},
        redactor.sanitize,
        default_model="model-a",
        context_max_chars=30_000,
    )
    app = create_app(
        coordinator,
        static,
        voice_conversation_service=conversations,
        voice_agent_profile_service=profiles,
        origins=frozenset({"http://testserver"}),
        trusted_hosts=["testserver"],
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/speech/agent-profiles/catalog").status_code == 403
        token = client.get("/api/v1/bootstrap").json()["csrf_token"]
        headers = {"Origin": "http://testserver", "X-Harness-CSRF": token}
        payload = asdict(
            replace(
                _spec(),
                name="API Reader",
                workspace_id=workspace_id,
                instructions="safe",
            )
        )
        payload["allowed_tools"] = ["read_file"]
        created = client.post("/api/v1/speech/agent-profiles", headers=headers, json=payload)
        assert created.status_code == 200
        profile_id = created.json()["profile_id"]
        assert client.get("/api/v1/speech/agent-profiles/catalog").status_code == 200
        assert client.get("/api/v1/speech/agent-profiles").json()[0]["available"] is True
        assert (
            client.get(f"/api/v1/speech/agent-profiles/{profile_id}").json()["name"] == "API Reader"
        )
        assert (
            client.get("/api/v1/speech/agent-profiles/ffffffffffffffffffffffffffffffff").status_code
            == 404
        )
        assert (
            client.post(
                "/api/v1/speech/agent-profiles",
                headers=headers,
                json={**payload, "extra": True},
            ).status_code
            == 422
        )
        cloned = client.post(f"/api/v1/speech/agent-profiles/{profile_id}/clone", headers=headers)
        assert cloned.status_code == 200
        assert (
            client.post(
                "/api/v1/tasks/ffffffffffffffffffffffffffffffff/cancel",
                headers=headers,
                json={"client_id": "browser-client-0001"},
            ).status_code
            == 409
        )
        conversation = client.post(
            "/api/v1/speech/conversations",
            headers=headers,
            json={"profile_id": profile_id},
        )
        assert conversation.status_code == 200
        body = conversation.json()
        assert body["mode"] == "agent"
        assert body["snapshot"]["allowed_tools"] == ["read_file"]
        updated = client.patch(
            f"/api/v1/speech/agent-profiles/{profile_id}",
            headers=headers,
            json={**payload, "name": "API Reader revised"},
        )
        assert updated.json()["revision"] == 2
        assert (
            client.get(f"/api/v1/speech/conversations/{body['conversation_id']}").json()[
                "configuration_status"
            ]
            == "outdated"
        )
        upgraded = client.post(
            f"/api/v1/speech/conversations/{body['conversation_id']}/profile-upgrade",
            headers=headers,
            json={"profile_id": profile_id, "revision": 2},
        )
        assert upgraded.json()["profile_revision"] == 2
        assert (
            client.post(
                f"/api/v1/speech/conversations/{body['conversation_id']}/turns",
                headers=headers,
                json={"text": "must not bypass the agent"},
            ).status_code
            == 400
        )
        agent_model = DirectModel()
        coordinator.state(workspace_id).runtime.model_client = cast(Any, agent_model)
        submitted = client.post(
            f"/api/v1/speech/conversations/{body['conversation_id']}/agent-turns",
            headers=headers,
            json={"text": "inspect the workspace", "client_id": "browser-client-0001"},
        )
        assert submitted.status_code == 202
        task_id = submitted.json()["task"]["task_id"]
        deadline = time.monotonic() + 5
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        while task["state"] not in {"completed", "failed"} and time.monotonic() < deadline:
            time.sleep(0.02)
            task = client.get(f"/api/v1/tasks/{task_id}").json()
        assert task["state"] == "completed"
        assert agent_model.calls == 1
        transcript = client.get(f"/api/v1/speech/conversations/{body['conversation_id']}").json()[
            "messages"
        ]
        assert [message["role"] for message in transcript] == ["user", "assistant"]
        assert (
            client.request(
                "DELETE",
                f"/api/v1/speech/agent-profiles/{profile_id}",
                headers=headers,
                json={"confirmation": profile_id},
            ).status_code
            == 200
        )
