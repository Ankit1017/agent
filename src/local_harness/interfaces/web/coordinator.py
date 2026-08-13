"""Coordinate isolated workspace runtimes and browser-submitted tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import Event

from local_harness.bootstrap import Runtime, build_runtime
from local_harness.config import Settings
from local_harness.domain.errors import HarnessError, SessionError, TaskCancelledError
from local_harness.domain.models import Session
from local_harness.domain.voice_agent import VoiceAgentExecutionPolicy
from local_harness.domain.web_ui import WebEvent, WebTask, WorkspaceEntry
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.identifiers import new_session_id
from local_harness.infrastructure.workspace_catalog import JsonWorkspaceCatalog
from local_harness.interfaces.web.bridge import WebPresentationBridge
from local_harness.interfaces.web.events import WebEventHub


@dataclass(slots=True)
class WorkspaceState:
    """Lazily composed services and exclusive mutation state for one workspace."""

    entry: WorkspaceEntry
    runtime: Runtime
    bridge: WebPresentationBridge
    active_task_id: str = ""


class WebRuntimeCoordinator:
    """Own workspace runtimes, task scheduling, and browser event routing."""

    def __init__(
        self,
        control_workspace: Path,
        catalog_path: Path,
        *,
        maximum_concurrency: int = 2,
        approval_timeout_seconds: int = 600,
    ) -> None:
        """Create a local multi-workspace coordinator."""
        self.control_workspace = control_workspace.resolve(strict=True)
        self.settings = Settings.load(self.control_workspace)
        self.catalog = JsonWorkspaceCatalog(catalog_path, self.control_workspace)
        self.hub = WebEventHub()
        self._states: dict[str, WorkspaceState] = {}
        self._tasks: dict[str, WebTask] = {}
        self._cancellations: dict[str, Event] = {}
        self._task_policies: dict[str, VoiceAgentExecutionPolicy] = {}
        self._semaphore = asyncio.Semaphore(maximum_concurrency)
        self._approval_timeout = approval_timeout_seconds
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the server event loop before task execution begins."""
        self._loop = loop

    def workspaces(self) -> list[WorkspaceEntry]:
        """Return configured workspace entries."""
        return self.catalog.list_entries()

    def state(self, workspace_id: str) -> WorkspaceState:
        """Lazily compose an isolated runtime for one allowlisted workspace."""
        entry = self.catalog.get(workspace_id)
        existing = self._states.get(workspace_id)
        if existing is not None:
            return existing
        bridge_holder: list[WebPresentationBridge] = []

        def presentation_factory(
            redactor: SecretRedactor,
        ) -> tuple[
            WebPresentationBridge,
            WebPresentationBridge,
            WebPresentationBridge,
            WebPresentationBridge,
        ]:
            bridge = WebPresentationBridge(
                redactor,
                self.hub,
                workspace_id,
                approval_timeout_seconds=self._approval_timeout,
            )
            bridge_holder.append(bridge)
            return bridge, bridge, bridge, bridge

        runtime = build_runtime(
            Path(entry.path),
            presentation_factory=presentation_factory,
            settings_override=self.settings,
        )
        state = WorkspaceState(entry, runtime, bridge_holder[0])
        self._states[workspace_id] = state
        return state

    def is_busy(self, workspace_id: str) -> bool:
        """Return whether one workspace currently owns an agent task."""
        state = self._states.get(workspace_id)
        return state is not None and bool(state.active_task_id)

    def require_idle(self, workspace_id: str) -> WorkspaceState:
        """Return a workspace state only when mutations are safe."""
        state = self.state(workspace_id)
        if state.active_task_id or state.bridge.has_pending():
            raise SessionError("Workspace is busy")
        return state

    async def submit(
        self,
        workspace_id: str,
        session_id: str,
        prompt: str,
        client_id: str,
        workflow_id: str | None = None,
        policy: VoiceAgentExecutionPolicy | None = None,
    ) -> WebTask:
        """Schedule one request while enforcing workspace and global bounds."""
        state = self.state(workspace_id)
        if state.active_task_id:
            raise SessionError("A request is already running in this workspace")
        session = state.runtime.sessions.load(session_id)
        task = WebTask(new_session_id(), workspace_id, session_id, client_id, "queued")
        self._tasks[task.task_id] = task
        self._cancellations[task.task_id] = Event()
        if policy is not None:
            self._task_policies[task.task_id] = policy
        state.active_task_id = task.task_id
        await self.hub.publish(
            WebEvent(
                0,
                "task.queued",
                workspace_id,
                session_id,
                task.task_id,
                payload={"state": "queued"},
            )
        )
        asyncio.create_task(self._run(state, session, prompt, task, workflow_id))
        return task

    def task(self, task_id: str) -> WebTask:
        """Return one known in-memory task state."""
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise SessionError("Task was not found") from exc

    async def cancel(self, task_id: str, client_id: str) -> WebTask:
        """Request owner-bound cooperative cancellation and reject approvals."""
        task = self.task(task_id)
        if task.client_id != client_id:
            raise SessionError("Task cancellation owner did not match")
        if task.state in {"completed", "failed", "cancelled"}:
            return task
        self._cancellations[task_id].set()
        state = self._states.get(task.workspace_id)
        if state is not None:
            state.bridge.reject_owner(client_id, "task cancelled")
        cancelling = replace(task, state="cancelling")
        self._tasks[task_id] = cancelling
        await self.hub.publish(
            WebEvent(
                0,
                "task.cancelling",
                task.workspace_id,
                task.session_id,
                task.task_id,
                payload={"state": "cancelling"},
            )
        )
        return cancelling

    def resolve_approval(
        self,
        workspace_id: str,
        approval_id: str,
        client_id: str,
        approved: bool,
        feedback: str,
    ) -> bool:
        """Resolve a workspace approval for its owning client."""
        return self.state(workspace_id).bridge.resolve(approval_id, client_id, approved, feedback)

    def disconnect(self, client_id: str) -> None:
        """Default pending approvals to rejection when an owner disconnects."""
        for state in self._states.values():
            state.bridge.reject_owner(client_id)

    def shutdown(self) -> None:
        """Reject pending approvals during server shutdown."""
        for state in self._states.values():
            state.bridge.reject_all()

    async def run_auxiliary(
        self,
        workspace_id: str,
        session_id: str,
        client_id: str,
        action: str,
        operation: Callable[[], object],
    ) -> object:
        """Run one exclusive synchronous session action with approval routing."""
        state = self.require_idle(workspace_id)
        if self._loop is None:
            raise RuntimeError("Web coordinator is not bound to an event loop")
        task_id = new_session_id()
        state.active_task_id = task_id
        async with self._semaphore:
            state.bridge.activate(self._loop, task_id, session_id, client_id)
            await self.hub.publish(
                WebEvent(
                    0,
                    "task.started",
                    workspace_id,
                    session_id,
                    task_id,
                    payload={"state": "running", "action": action},
                )
            )
            try:
                result = await asyncio.to_thread(operation)
                await self.hub.publish(
                    WebEvent(
                        0,
                        "task.completed",
                        workspace_id,
                        session_id,
                        task_id,
                        payload={"action": action},
                    )
                )
                return result
            finally:
                state.bridge.deactivate()
                state.active_task_id = ""

    async def _run(
        self,
        state: WorkspaceState,
        session: Session,
        prompt: str,
        task: WebTask,
        workflow_id: str | None,
    ) -> None:
        if self._loop is None:
            raise RuntimeError("Web coordinator is not bound to an event loop")
        async with self._semaphore:
            running = replace(task, state="running")
            self._tasks[task.task_id] = running
            cancellation = self._cancellations[task.task_id]
            policy = self._task_policies.get(task.task_id)
            agent = state.runtime.agent(session, policy, cancellation_requested=cancellation.is_set)
            state.bridge.activate(self._loop, task.task_id, task.session_id, task.client_id)
            await self.hub.publish(
                WebEvent(
                    0,
                    "task.started",
                    task.workspace_id,
                    task.session_id,
                    task.task_id,
                    request_number=agent.next_request_number,
                    payload={"state": "running"},
                )
            )
            try:
                response = await asyncio.to_thread(agent.submit, prompt, workflow_id)
            except TaskCancelledError:
                cancelled = replace(running, state="cancelled", error="Task was cancelled")
                self._tasks[task.task_id] = cancelled
                await self.hub.publish(
                    WebEvent(
                        0,
                        "task.cancelled",
                        task.workspace_id,
                        task.session_id,
                        task.task_id,
                        payload={"state": "cancelled"},
                    )
                )
            except HarnessError as exc:
                failed = replace(running, state="failed", error=str(exc))
                self._tasks[task.task_id] = failed
                await self.hub.publish(
                    WebEvent(
                        0,
                        "task.failed",
                        task.workspace_id,
                        task.session_id,
                        task.task_id,
                        payload={"error": state.runtime.redactor.redact(str(exc))},
                    )
                )
            except Exception:
                failed = replace(running, state="failed", error="Unexpected task failure")
                self._tasks[task.task_id] = failed
                await self.hub.publish(
                    WebEvent(
                        0,
                        "task.failed",
                        task.workspace_id,
                        task.session_id,
                        task.task_id,
                        payload={"error": "Unexpected task failure"},
                    )
                )
            else:
                if cancellation.is_set():
                    cancelled = replace(running, state="cancelled", error="Task was cancelled")
                    self._tasks[task.task_id] = cancelled
                    await self.hub.publish(
                        WebEvent(
                            0,
                            "task.cancelled",
                            task.workspace_id,
                            task.session_id,
                            task.task_id,
                            payload={"state": "cancelled"},
                        )
                    )
                    return
                completed = replace(running, state="completed", response=response)
                self._tasks[task.task_id] = completed
                await self.hub.publish(
                    WebEvent(
                        0,
                        "task.completed",
                        task.workspace_id,
                        task.session_id,
                        task.task_id,
                        payload={"response": state.runtime.redactor.redact(response)},
                    )
                )
            finally:
                state.bridge.deactivate()
                state.active_task_id = ""
                self._cancellations.pop(task.task_id, None)
                self._task_policies.pop(task.task_id, None)


def serialize_task(task: WebTask) -> dict[str, object]:
    """Return one JSON-compatible task record."""
    return asdict(task)
