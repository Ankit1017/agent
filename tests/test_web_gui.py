"""Offline tests for the localhost browser presentation boundary."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from local_harness.domain.errors import ModelError, PolicyViolation, SessionError
from local_harness.domain.models import Message, ProgressEvent, ToolDefinition
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.guardrails.workspace_catalog_policy import WorkspaceCatalogPolicy
from local_harness.infrastructure.workspace_catalog import JsonWorkspaceCatalog
from local_harness.interfaces.web.api import create_app
from local_harness.interfaces.web.bridge import WebPresentationBridge
from local_harness.interfaces.web.coordinator import WebRuntimeCoordinator
from local_harness.interfaces.web.events import WebEventHub


class FinalModel:
    """Return one deterministic final response without network access."""

    def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> Message:
        """Return a protocol-compliant assistant answer."""
        return Message(
            role="assistant",
            content="<step_summary>Completed browser request</step_summary>\nBrowser answer",
        )


class ExpectedFailureModel:
    """Raise one safe provider failure."""

    def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> Message:
        """Raise a domain error for browser translation testing."""
        raise ModelError("Provider unavailable")


class UnexpectedFailureModel:
    """Raise one unexpected provider failure."""

    def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> Message:
        """Raise an internal exception that must not leak."""
        raise RuntimeError("secret internal detail")


class CandidateModel:
    """Return one valid controlled-improvement proposal."""

    model = "gpt-oss:20b"

    def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> Message:
        """Return a bounded structured proposal without network access."""
        del messages, tools
        return Message(
            "assistant",
            json.dumps(
                {
                    "proposal": "Reduce repeated project reads",
                    "predicted_changes": ["tokens -10%"],
                    "evidence_ids": ["fixture"],
                    "risks": ["stale context"],
                    "rollback_instructions": "Restore the previous profile",
                    "required_suite": "core",
                }
            ),
        )


def _environment(tmp_path: Path) -> tuple[WebRuntimeCoordinator, Path]:
    tmp_path.joinpath(".env").write_text(
        "OPENAI_API_KEY=sk-local-real-test-key\n"
        "OPENAI_BASE_URL=http://127.0.0.1:4000/v1\n"
        "OPENAI_MODEL=gpt-5.5\n"
        "HARNESS_MODELS=gpt-5.5,gpt-oss:20b\n"
        "SEARXNG_BASE_URL=http://127.0.0.1:8080\n",
        encoding="utf-8",
    )
    static = tmp_path / "static"
    static.mkdir()
    static.joinpath("index.html").write_text("<h1>Harness</h1>", encoding="utf-8")
    return WebRuntimeCoordinator(tmp_path, tmp_path / "catalog.json"), static


def _csrf(client: TestClient) -> tuple[str, dict[str, str]]:
    response = client.get("/api/v1/bootstrap")
    assert response.status_code == 200
    token = response.json()["csrf_token"]
    return token, {"Origin": "http://testserver", "X-Harness-CSRF": token}


def test_workspace_catalog_validates_and_persists(tmp_path: Path) -> None:
    """Catalog additions are canonical, atomic, unique, and non-destructive."""
    control = tmp_path / "control"
    project = tmp_path / "project"
    control.mkdir()
    project.mkdir()
    catalog_path = tmp_path / "catalog.json"
    catalog = JsonWorkspaceCatalog(catalog_path, control)

    assert len(catalog.list_entries()) == 1
    label, resolved = catalog.validate("  My   Project ", str(project))
    assert label == "My Project"
    entry = catalog.add(label, resolved)
    assert JsonWorkspaceCatalog(catalog_path, control).get(entry.workspace_id).path == str(project)
    with pytest.raises(SessionError, match="already"):
        catalog.validate("Again", str(project))
    assert catalog.remove(entry.workspace_id) == entry
    assert project.exists()
    with pytest.raises(SessionError, match="control"):
        catalog.remove(catalog.list_entries()[0].workspace_id)


def test_workspace_catalog_rejects_invalid_inputs(tmp_path: Path) -> None:
    """Workspace paths remain local, existing, non-root, and explicitly labelled."""
    policy = WorkspaceCatalogPolicy()
    with pytest.raises(PolicyViolation, match="absolute"):
        policy.resolve("relative")
    with pytest.raises(PolicyViolation, match="exist"):
        policy.resolve(str(tmp_path / "missing"))
    with pytest.raises(PolicyViolation, match="root"):
        policy.resolve(str(Path(tmp_path.anchor)))
    catalog_path = tmp_path / "bad.json"
    catalog_path.write_text('{"schema_version":99}', encoding="utf-8")
    with pytest.raises(SessionError, match="malformed"):
        JsonWorkspaceCatalog(catalog_path, tmp_path)


def test_browser_api_security_workspace_and_session_flows(tmp_path: Path) -> None:
    """REST resources require CSRF and expose safe workspace/session operations."""
    coordinator, static = _environment(tmp_path)
    app = create_app(
        coordinator,
        static,
        origins=frozenset({"http://testserver"}),
        trusted_hosts=["testserver"],
    )
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "healthy"
        assert "Harness" in client.get("/").text
        legacy = client.get("/api/config")
        assert legacy.status_code == 410
        assert legacy.headers["clear-site-data"] == '"cache", "storage"'
        legacy_error = client.get("/error", follow_redirects=False)
        assert legacy_error.status_code == 307
        assert legacy_error.headers["location"] == "/"
        assert client.get("/_app/immutable/old.js").status_code == 410
        assert client.get("/static/old.css").status_code == 410
        shell = client.get("/")
        assert shell.headers["cache-control"] == "no-store"
        policy = shell.headers["content-security-policy"]
        assert "connect-src 'self' blob:" in policy
        assert "img-src 'self' data: blob:" in policy
        _, headers = _csrf(client)
        workspace_id = client.get("/api/v1/workspaces").json()[0]["workspace_id"]
        assert client.post(f"/api/v1/workspaces/{workspace_id}/sessions").status_code == 403
        created = client.post(f"/api/v1/workspaces/{workspace_id}/sessions", headers=headers).json()
        session_id = created["session_id"]
        assert client.get(f"/api/v1/workspaces/{workspace_id}/sessions").json()
        detail = client.get(f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}").json()
        assert detail["session_id"] == session_id
        assert detail["model"] == "gpt-5.5"
        selected_model = client.put(
            f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/model",
            headers=headers,
            json={"model": "gpt-oss:20b"},
        ).json()
        assert selected_model["model"] == "gpt-oss:20b"
        assert (
            client.put(
                f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/model",
                headers=headers,
                json={"model": "not-configured"},
            ).status_code
            == 400
        )
        assert (
            client.put(
                f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/max-turns",
                headers=headers,
                json={"value": 30},
            ).json()["value"]
            == 30
        )
        assert (
            client.put(
                f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/quota",
                headers=headers,
                json={"value": 5000},
            ).json()["value"]
            == 5000
        )
        exported = client.post(
            f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/export",
            headers=headers,
            json={"format": "md"},
        ).json()
        assert exported["path"].endswith(".md")
        assert client.get(f"/api/v1/workspaces/{workspace_id}/plugins").status_code == 200
        evaluation_status = client.get(
            f"/api/v1/workspaces/{workspace_id}/evaluations/status"
        ).json()
        assert evaluation_status["enabled"] is True
        assert client.get(f"/api/v1/workspaces/{workspace_id}/evaluations/history").json() == []
        assert client.get(f"/api/v1/workspaces/{workspace_id}/candidates").json() == []
        memory_status = client.get(f"/api/v1/workspaces/{workspace_id}/project-memory").json()
        assert memory_status["generation"] == 0
        workflows = client.get(f"/api/v1/workspaces/{workspace_id}/workflows?query=review").json()
        assert "review_changes" in {item["workflow_id"] for item in workflows}
        workflow_url = f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/workflow"
        assert client.put(workflow_url, json={"workflow_id": "review_changes"}).status_code == 403
        pending = client.put(
            workflow_url,
            headers=headers,
            json={"workflow_id": "review_changes"},
        ).json()
        assert pending["pending_workflow_override"] == "review_changes"
        assert client.get(workflow_url).json()["pending_workflow_override"] == "review_changes"
        assert client.put(workflow_url, headers=headers, json={"workflow_id": None}).json() == {
            "pending_workflow_override": None
        }
        assert client.get(f"{workflow_url}?request_number=1").json()["workflow"] is None
        assert client.get(f"/api/v1/workspaces/{workspace_id}/integrity").status_code == 200
        assert client.get(f"/api/v1/workspaces/{workspace_id}/archives").json() == []
        command_url = f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/commands"
        for command in (
            "/help",
            "/sessions",
            "/events",
            "/session-info",
            "/max-turns 22",
            "/models",
            "/model",
            "/model gpt-oss:20b",
            "/quota 9000",
            "/plugins",
            "/workflows",
            "/workflow use review_changes",
            "/workflow status",
            "/eval status",
            "/eval run core",
            "/handoff",
            "/index",
            "/archives",
            "/export csv",
            "/session-check",
            "/exit",
        ):
            response = client.post(
                command_url,
                headers=headers,
                json={"value": command, "client_id": "browser-client-0001"},
            )
            assert response.status_code == 200, response.text

        history = client.get(
            f"/api/v1/workspaces/{workspace_id}/evaluations/history?limit=25"
        ).json()
        assert len(history) == 20
        observed = history[0]
        evaluation_detail = client.get(
            f"/api/v1/workspaces/{workspace_id}/sessions/{observed['session_id']}"
            f"/evaluations/{observed['request_number']}"
        ).json()
        assert evaluation_detail["observation"]["observation_id"] == observed["observation_id"]
        marked = client.put(
            f"/api/v1/workspaces/{workspace_id}/sessions/{observed['session_id']}/evaluations/mark",
            headers=headers,
            json={"request_number": observed["request_number"], "outcome": "pass", "note": "ok"},
        )
        assert marked.status_code == 200

        cast(Any, coordinator.state(workspace_id).runtime).model_client = CandidateModel()
        proposed = client.post(
            f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/candidates",
            headers=headers,
            json={"component_id": "tool_profiles", "client_id": "browser-client-0001"},
        )
        assert proposed.status_code == 200, proposed.text
        candidate_id = proposed.json()["candidate"]["candidate_id"]
        assert client.get(f"/api/v1/workspaces/{workspace_id}/candidates").json()
        decided = client.put(
            f"/api/v1/workspaces/{workspace_id}/candidates/{candidate_id}",
            headers=headers,
            json={"approved": False, "feedback": "needs evidence"},
        )
        assert decided.json()["candidate"]["status"] == "rejected"
        assert (
            client.post(
                command_url,
                headers=headers,
                json={"value": "/not-real", "client_id": "browser-client-0001"},
            ).status_code
            == 400
        )


def test_browser_workspace_confirmation_and_removal(tmp_path: Path) -> None:
    """Registration uses a short-lived challenge and removal preserves files."""
    coordinator, static = _environment(tmp_path)
    project = tmp_path / "another-project"
    project.mkdir()
    app = create_app(
        coordinator,
        static,
        origins=frozenset({"http://testserver"}),
        trusted_hosts=["testserver"],
    )
    with TestClient(app) as client:
        _, headers = _csrf(client)
        proposed = client.post(
            "/api/v1/workspaces/validate",
            headers=headers,
            json={"label": "Another", "path": str(project)},
        )
        assert proposed.status_code == 200
        confirmed = client.post(
            "/api/v1/workspaces/confirm",
            headers=headers,
            json={"challenge_id": proposed.json()["challenge_id"], "approved": True},
        )
        workspace_id = confirmed.json()["workspace_id"]
        removed = client.delete(f"/api/v1/workspaces/{workspace_id}", headers=headers)
        assert removed.json()["data_deleted"] is False
        assert project.exists()
        assert (
            client.post(
                "/api/v1/workspaces/confirm",
                headers=headers,
                json={"challenge_id": "missing", "approved": True},
            ).status_code
            == 409
        )


def test_browser_prompt_stream_and_event_mutations(tmp_path: Path) -> None:
    """A browser request runs offline and its persisted progress is queryable and taggable."""
    coordinator, static = _environment(tmp_path)
    app = create_app(
        coordinator,
        static,
        origins=frozenset({"http://testserver"}),
        trusted_hosts=["testserver"],
    )
    with TestClient(app) as client:
        _, headers = _csrf(client)
        workspace_id = client.get("/api/v1/workspaces").json()[0]["workspace_id"]
        state = coordinator.state(workspace_id)
        state.runtime.model_client = FinalModel()  # type: ignore[assignment]
        session_id = client.post(
            f"/api/v1/workspaces/{workspace_id}/sessions", headers=headers
        ).json()["session_id"]
        submitted = client.post(
            f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/requests",
            headers=headers,
            json={
                "prompt": "Answer locally with api_key=super-secret-value",
                "client_id": "browser-client-0001",
            },
        )
        assert submitted.status_code == 202
        assert submitted.json()["redacted"] is True
        assert "super-secret-value" not in submitted.json()["display_prompt"]
        task_id = submitted.json()["task_id"]
        for _ in range(100):
            task = client.get(f"/api/v1/tasks/{task_id}").json()
            if task["state"] == "completed":
                break
            time.sleep(0.01)
        assert task["response"] == "Browser answer"
        events = client.get(
            f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/events?count=20"
        ).json()
        assert events[-1]["target"] == "final"
        sequence = events[-1]["sequence"]
        assert (
            client.post(
                f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/events/{sequence}/tags",
                headers=headers,
                json={"label": "done"},
            ).json()["tag"]
            == "done"
        )
        assert (
            client.delete(
                f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/events/{sequence}/tags/done",
                headers=headers,
            ).status_code
            == 200
        )
        command_url = f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/commands"
        for command in (
            f"/tag add {sequence} checked",
            "/tags checked",
            f"/tag remove {sequence} checked",
            "/max-turns reset",
            "/quota reset",
            f"/resume {session_id}",
            "/new",
        ):
            assert (
                client.post(
                    command_url,
                    headers=headers,
                    json={"value": command, "client_id": "browser-client-0001"},
                ).status_code
                == 200
            )


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (ExpectedFailureModel(), "Provider unavailable"),
        (UnexpectedFailureModel(), "Unexpected task failure"),
    ],
)
def test_browser_task_failures_are_safe(tmp_path: Path, model: object, expected: str) -> None:
    """Expected and unexpected worker failures produce bounded safe task states."""
    coordinator, static = _environment(tmp_path)
    app = create_app(
        coordinator,
        static,
        origins=frozenset({"http://testserver"}),
        trusted_hosts=["testserver"],
    )
    with TestClient(app) as client:
        _, headers = _csrf(client)
        workspace_id = client.get("/api/v1/workspaces").json()[0]["workspace_id"]
        coordinator.state(workspace_id).runtime.model_client = model  # type: ignore[assignment]
        session_id = client.post(
            f"/api/v1/workspaces/{workspace_id}/sessions", headers=headers
        ).json()["session_id"]
        submitted = client.post(
            f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/requests",
            headers=headers,
            json={"prompt": "Fail safely", "client_id": "browser-client-0001"},
        )
        task_id = submitted.json()["task_id"]
        for _ in range(100):
            task = client.get(f"/api/v1/tasks/{task_id}").json()
            if task["state"] == "failed":
                break
            time.sleep(0.01)
        assert task["error"] == expected
        assert "secret internal detail" not in str(task)


def test_websocket_stream_replays_task_events(tmp_path: Path) -> None:
    """The same-origin socket receives ordered live lifecycle events."""
    coordinator, static = _environment(tmp_path)
    app = create_app(
        coordinator,
        static,
        origins=frozenset({"http://testserver"}),
        trusted_hosts=["testserver"],
    )
    with TestClient(app) as client:
        _, headers = _csrf(client)
        workspace_id = client.get("/api/v1/workspaces").json()[0]["workspace_id"]
        coordinator.state(workspace_id).runtime.model_client = FinalModel()  # type: ignore[assignment]
        session_id = client.post(
            f"/api/v1/workspaces/{workspace_id}/sessions", headers=headers
        ).json()["session_id"]
        with client.websocket_connect(
            "/api/v1/stream?client_id=browser-client-0001&after=0",
            headers={"origin": "http://testserver"},
        ) as socket:
            client.post(
                f"/api/v1/workspaces/{workspace_id}/sessions/{session_id}/requests",
                headers=headers,
                json={"prompt": "Stream", "client_id": "browser-client-0001"},
            )
            kinds = []
            for _ in range(10):
                kind = socket.receive_json()["type"]
                kinds.append(kind)
                if kind == "task.completed":
                    break
        assert kinds[0] == "task.queued"
        assert "task.started" in kinds
        assert kinds[-1] == "task.completed"


def test_web_server_launcher_is_loopback_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command launcher constructs the app and never accepts a public bind."""
    from local_harness.interfaces.web import server

    coordinator, static = _environment(tmp_path)
    del coordinator
    catalog = tmp_path / "launcher-catalog.json"
    called: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        called.update(kwargs)
        called["app"] = app

    monkeypatch.setattr("uvicorn.run", fake_run)
    server.main(
        [
            "--control-workspace",
            str(tmp_path),
            "--catalog",
            str(catalog),
            "--static-dir",
            str(static),
            "--port",
            "3010",
        ]
    )
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 3010
    with pytest.raises(SystemExit):
        server.main(["--host", "0.0.0.0"])


@pytest.mark.asyncio
async def test_web_bridge_routes_and_owns_approvals() -> None:
    """Only the originating browser resolves an approval; disconnect rejects it."""
    hub = WebEventHub(queue_limit=10)
    bridge = WebPresentationBridge(SecretRedactor(), hub, "workspace", approval_timeout_seconds=2)
    queue = await hub.subscribe("observer", 0)
    bridge.activate(asyncio.get_running_loop(), "task", "session", "owner-client-0001")
    bridge.publish(ProgressEvent(1, 1, "model_start", "Waiting", "model", "started"))
    assert (await asyncio.wait_for(queue.get(), 1)).type == "progress"

    future = asyncio.create_task(
        asyncio.to_thread(bridge.request, "Get-Date", "Show time", "C:\\work")
    )
    requested = await asyncio.wait_for(queue.get(), 1)
    assert requested.type == "approval.requested"
    approval_id = str(requested.payload["approval_id"])
    assert not bridge.resolve(approval_id, "wrong-client-0001", True)
    assert bridge.resolve(approval_id, "owner-client-0001", True)
    assert (await future).approved

    second = asyncio.create_task(
        asyncio.to_thread(bridge.request_patch, "diff", "edit", "C:\\work")
    )
    await asyncio.wait_for(queue.get(), 1)  # resolution from the first request
    await asyncio.wait_for(queue.get(), 1)  # second approval request
    bridge.reject_owner("owner-client-0001")
    assert not (await second).approved
    bridge.deactivate()
    await hub.unsubscribe("observer")


@pytest.mark.asyncio
async def test_event_hub_replays_and_bounds_slow_clients() -> None:
    """Reconnect replay is ordered and slow subscribers receive a resync signal."""
    from local_harness.domain.web_ui import WebEvent

    hub = WebEventHub(history_limit=3, queue_limit=1)
    await hub.publish(WebEvent(0, "one"))
    replay = await hub.subscribe("client", 0)
    assert (await replay.get()).type == "one"
    await hub.publish(WebEvent(0, "two"))
    await hub.publish(WebEvent(0, "three"))
    assert (await replay.get()).type == "resync_required"
