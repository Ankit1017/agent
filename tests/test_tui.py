"""Headless interaction tests for the Textual presentation layer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any, cast

from textual.widgets import DataTable, Input, Markdown, Static, TextArea

from local_harness.application.agent import AgentService
from local_harness.bootstrap import Runtime
from local_harness.domain.maintenance import ArchiveInfo, ExportResult, PluginStatus
from local_harness.domain.models import ApprovalDecision, Message, ProgressEvent, Session
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.interfaces.tui.activity import RequestActivity
from local_harness.interfaces.tui.app import HarnessApp, _format_activity_event, _short_workspace
from local_harness.interfaces.tui.bridge import TuiBridge
from local_harness.interfaces.tui.screens import (
    ApprovalScreen,
    EventsScreen,
    HelpScreen,
    MaintenanceApprovalScreen,
    PatchApprovalScreen,
    SessionsScreen,
)


@dataclass
class FakeAgent:
    """Immediate agent double for deterministic UI interaction."""

    session: Session
    max_turns: int = 20
    max_turns_source: str = "default"
    token_usage: int = 12
    token_budget: int = 0

    @property
    def next_request_number(self) -> int:
        """Return the next request number for the UI placeholder."""
        return sum(message.role == "user" for message in self.session.messages) + 1

    def submit(self, value: str) -> str:
        """Return a Markdown response and one observable event."""
        self.session.events.append(
            ProgressEvent(1, 1, "model_complete", "Answered request", "final", "success", 100)
        )
        return f"## Result\n\nReply: {value}"

    def configure_max_turns(self, value: int | None) -> int:
        """Apply a test-only call limit."""
        self.max_turns = 20 if value is None else value
        self.max_turns_source = "default" if value is None else "session"
        return self.max_turns

    def configure_token_budget(self, value: int | None) -> int:
        """Apply a test-only advisory budget."""
        self.token_budget = value or 0
        return self.token_budget

    def sanitize_input(self, value: str) -> tuple[str, bool]:
        """Return unchanged test input."""
        return value, False

    def summarize_with_model(self) -> str:
        """Return one explicit fake summary."""
        return "Session summarized"


class FakeSessionService:
    """Capture productivity commands used by the TUI."""

    def add_tag(self, session: Session, sequence: int, label: str) -> str:
        """Attach a test tag."""
        session.events[0] = replace(session.events[0], tags=(label,))
        return label

    def remove_tag(self, session: Session, sequence: int, label: str) -> str:
        """Remove a test tag."""
        return label

    def tagged_events(self, session: Session, label: str = "") -> list[ProgressEvent]:
        """Return the current events."""
        return session.events

    def export(self, session: Session, format_name: str) -> ExportResult:
        """Return a fake export path."""
        return ExportResult("E:\\workspace\\.harness\\exports\\session.md", format_name)

    def list_archives(self) -> list[ArchiveInfo]:
        """Return one fake archive."""
        return [ArchiveInfo("b" * 32, "2026-01-01", "summary", 10)]

    def restore(self, session_id: str) -> Session:
        """Return a restored session."""
        return Session(session_id, "E:\\workspace", "model")

    def archive(self, session_id: str) -> ArchiveInfo:
        """Return one archived record."""
        return ArchiveInfo(session_id, "2026-01-01", "summary", 10)

    def scan(self) -> list[object]:
        """Return no integrity findings."""
        return []


class FakeSessions:
    """In-memory session repository for UI commands."""

    def __init__(self) -> None:
        """Create one resumable session."""
        self.saved = Session("a" * 32, "E:\\workspace", "model")

    def list_sessions(self) -> list[Session]:
        """List the saved session."""
        return [self.saved]

    def load(self, session_id: str) -> Session:
        """Load the known saved session."""
        if session_id != self.saved.session_id:
            raise ValueError("missing")
        return self.saved


class FakeRuntime:
    """Runtime surface consumed by the TUI."""

    def __init__(self) -> None:
        """Create deterministic settings and session state."""
        self.workspace = Path("E:/workspace")
        self.settings = SimpleNamespace(
            model="model", models=("model", "other"), session_token_budget=0
        )
        self.sessions = FakeSessions()
        self.session_service = FakeSessionService()
        self.plugin_statuses = [PluginStatus("sample", "discovered")]
        self.integrity_findings: list[object] = []
        self.counter = 0

    def new_session(self) -> Session:
        """Create a new session."""
        self.counter += 1
        return Session(f"{self.counter:032x}", str(self.workspace), "model")

    def agent(self, session: Session) -> FakeAgent:
        """Bind a fake agent to a session."""
        return FakeAgent(session)

    def switch_model(self, session: Session, model: str | None) -> FakeAgent:
        """Persist a configured fake model selection."""
        session.model = self.settings.model if model is None else model
        return self.agent(session)


def _make_app(session: Session | None = None) -> HarnessApp:
    runtime = FakeRuntime()
    bridge = TuiBridge(SecretRedactor(("secret-value",)))
    return HarnessApp(
        cast(Runtime, runtime),
        cast(AgentService, FakeAgent(session or runtime.new_session())),
        bridge,
    )


def test_tui_submits_markdown_and_routes_overlays() -> None:
    """Composer submission and major keyboard overlays work headlessly."""

    async def scenario() -> None:
        app = _make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            composer = app.query_one("#composer", TextArea)
            composer.text = "hello"
            await pilot.press("ctrl+enter")
            await pilot.pause()
            await pilot.pause()
            assert len(app.query(Markdown)) == 1
            activities = app.query(RequestActivity)
            assert len(activities) == 1
            assert activities.first().collapsed
            assert activities.first().title.startswith("Completed")
            assert not composer.disabled

            await pilot.press("ctrl+h")
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("ctrl+e")
            await pilot.pause()
            assert isinstance(app.screen, EventsScreen)
            assert app.screen.query_one(DataTable).row_count == len(app.agent.session.events)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("ctrl+r")
            await pilot.pause()
            assert isinstance(app.screen, SessionsScreen)
            await pilot.press("escape")
            await pilot.pause()

            decisions: list[ApprovalDecision | None] = []
            app.request_approval(
                "Get-ChildItem", "Inspect files", "E:\\workspace", decisions.append
            )
            await pilot.pause()
            assert isinstance(app.screen, ApprovalScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert decisions == [ApprovalDecision(False)]

            app.request_approval(
                "Get-ChildItem", "Inspect files", "E:\\workspace", decisions.append
            )
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert decisions[-1] == ApprovalDecision(True)

            app.request_patch_approval(
                "--- a/app.py\n+++ b/app.py", "Edit app", "E:\\workspace", decisions.append
            )
            await pilot.pause()
            assert isinstance(app.screen, PatchApprovalScreen)
            assert "a/app.py" in str(app.screen.query_one("#patch-diff", Static).content)
            await pilot.press("escape")
            await pilot.pause()
            assert decisions[-1] == ApprovalDecision(False)

    asyncio.run(scenario())


def test_tui_commands_progress_and_responsive_sidebar() -> None:
    """Runtime commands update state and narrow terminals hide the sidebar."""

    async def scenario() -> None:
        app = _make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            assert app.has_class("narrow")
            composer = app.query_one("#composer", TextArea)
            composer.text = "/max-turns 30"
            await pilot.press("ctrl+enter")
            await pilot.pause()
            assert app.agent.max_turns == 30

            composer.text = "/model other"
            await pilot.press("ctrl+enter")
            await pilot.pause()
            assert app.agent.session.model == "other"

            event = ProgressEvent(
                1, 2, "tool_error", "Command failed", "run_powershell", "error", 250
            )
            app.show_progress(event)
            assert "ERROR" in str(app.query_one("#activity-current", Static).content)

            composer.text = "/new"
            await pilot.press("ctrl+enter")
            await pilot.pause()
            assert app.agent.session.session_id.endswith("2")

    asyncio.run(scenario())


def test_tui_productivity_commands_and_event_filter() -> None:
    """New analytics and session commands remain usable from the full-screen interface."""

    async def scenario() -> None:
        app = _make_app()
        app.agent.session.events.append(
            ProgressEvent(1, 1, "model_complete", "Done", "final", "success")
        )
        async with app.run_test(size=(120, 40)) as pilot:
            composer = app.query_one("#composer", TextArea)

            async def submit(command: str) -> None:
                composer.text = command
                await pilot.press("ctrl+enter")
                await pilot.pause()

            await submit("/session-info")
            await submit("/quota 100")
            assert app.agent.token_budget == 100
            await submit("/tag add 1 bug")
            await submit("/tags bug")
            assert isinstance(app.screen, EventsScreen)
            await pilot.press("escape")
            await pilot.pause()
            await submit("/export md")
            await submit("/archives")
            await submit(f"/restore {'b' * 32}")
            await submit("/plugins")
            await submit("/summarize")
            await pilot.pause()
            assert not app.query_one("#composer", TextArea).disabled
            await pilot.press("ctrl+f")
            await pilot.pause()
            assert isinstance(app.screen, EventsScreen)
            event_filter = app.screen.query_one("#event-filter", Input)
            event_filter.value = "model"
            await pilot.pause()
            assert app.screen.query_one(DataTable).row_count >= 1

    asyncio.run(scenario())


def test_maintenance_modal_defaults_to_rejection() -> None:
    """Maintenance confirmation is keyboard accessible and default-reject."""

    async def scenario() -> None:
        screen = MaintenanceApprovalScreen("Archive session", "abc")
        app = _make_app()
        decisions: list[ApprovalDecision | None] = []
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, decisions.append)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert decisions == [ApprovalDecision(False)]

    asyncio.run(scenario())


def test_workspace_shortening_preserves_tail() -> None:
    """Long workspace labels retain their most identifying suffix."""
    short = Path("E:/work")
    long = Path("E:/" + "folder/" * 10)
    assert _short_workspace(short) == str(short)
    assert _short_workspace(long).startswith("…")


def test_activity_event_format_contains_complete_observable_record() -> None:
    """Sidebar records retain time, source, call, status, summary, and target."""
    event = ProgressEvent(
        3,
        2,
        "tool_complete",
        "Read configuration",
        "read_file",
        "success",
        1250,
        "2026-08-09T12:34:56+00:00",
    )

    rendered = _format_activity_event(event)

    assert "12:34:56" in rendered
    assert "TOOL #2" in rendered
    assert "SUCCESS" in rendered
    assert "1.2s" in rendered
    assert "Read configuration → read_file" in rendered


def test_request_activity_merges_model_events_and_preserves_expansion() -> None:
    """Request timelines merge model lifecycle events and retain manual state."""
    activity = RequestActivity(
        1,
        [
            ProgressEvent(1, 1, "model_start", "Waiting", "model", "started", request_number=1),
            ProgressEvent(
                2,
                1,
                "model_complete",
                "Inspect files",
                "read_file",
                "success",
                1000,
                request_number=1,
            ),
            ProgressEvent(
                3,
                1,
                "tool_complete",
                "Read project file",
                "read_file",
                "success",
                500,
                request_number=1,
            ),
        ],
    )

    assert activity.collapsed
    assert activity.step_count == 1
    assert activity.total_duration_ms == 1500
    activity.collapsed = False
    activity.finish(failed=False)
    assert not activity.collapsed
    assert activity.title == "Completed · 1 step · 1.5s"


def test_tui_reconstructs_persisted_request_activity_before_answer() -> None:
    """Resumed grouped messages rebuild a collapsed timeline in conversation order."""
    session = Session(
        "b" * 32,
        "E:\\workspace",
        "model",
        messages=[
            Message(role="user", content="Inspect", request_number=1),
            Message(role="assistant", content="Done", request_number=1),
        ],
        events=[
            ProgressEvent(1, 1, "model_start", "Waiting", "model", "started", request_number=1),
            ProgressEvent(
                2,
                1,
                "model_complete",
                "Inspect project",
                "final",
                "success",
                800,
                request_number=1,
            ),
        ],
    )

    async def scenario() -> None:
        app = _make_app(session)
        async with app.run_test(size=(120, 40)):
            activity = app.query_one(RequestActivity)
            answer = app.query_one(".assistant-message", Markdown)
            conversation = list(app.query_one("#conversation").children)
            assert activity.collapsed
            assert activity.title == "Completed · 1 step · 0.8s"
            assert conversation.index(activity) < conversation.index(answer)

    asyncio.run(scenario())


def test_request_activity_distinguishes_recoverable_and_fatal_errors() -> None:
    """Tool issues produce a warning while fatal request errors stop the activity."""
    event = ProgressEvent(
        1,
        1,
        "tool_error",
        "Command timed out",
        "run_powershell",
        "error",
        1000,
        request_number=1,
    )
    warning = RequestActivity(1, [event])
    warning.finish(failed=False)
    fatal = RequestActivity(1, [event])
    fatal.finish(failed=True)

    assert warning.title == "Completed with issues · 1 step · 1.0s"
    assert fatal.title == "Stopped with error · 1 step"


class FakeBridgeApp:
    """Execute bridge callbacks synchronously without a Textual event loop."""

    def __init__(self, *, resolve_approval: bool) -> None:
        """Configure whether approval callbacks resolve immediately."""
        self.resolve_approval = resolve_approval
        self.events: list[ProgressEvent] = []
        self.approval_requested = Event()

    def call_from_thread(self, callback: Any, *args: Any) -> Any:
        """Invoke a callback immediately."""
        return callback(*args)

    def show_progress(self, event: ProgressEvent) -> None:
        """Capture a delivered progress event."""
        self.events.append(event)

    def request_approval(self, *args: Any) -> None:
        """Capture approval and optionally resolve it."""
        self.approval_requested.set()
        callback = args[-1]
        if self.resolve_approval:
            callback(ApprovalDecision(True))

    def request_patch_approval(self, *args: Any) -> None:
        """Capture patch approval and optionally resolve it."""
        self.request_approval(*args)

    def request_maintenance_approval(self, *args: Any) -> None:
        """Capture maintenance approval and optionally resolve it."""
        self.request_approval(*args)


def test_tui_bridge_redacts_and_rejects_pending_shutdown() -> None:
    """The bridge redacts progress and unblocks pending approval as rejected."""
    bridge = TuiBridge(SecretRedactor(("secret-value",)))
    immediate = FakeBridgeApp(resolve_approval=True)
    bridge.bind(cast(Any, immediate))
    bridge.publish(ProgressEvent(1, 1, "model_complete", "Used secret-value", "final", "success"))
    assert "secret-value" not in immediate.events[0].summary
    assert bridge.request("echo", "safe", "workspace") == ApprovalDecision(True)
    assert bridge.request_patch("secret-value diff", "safe", "workspace") == ApprovalDecision(True)
    assert bridge.request_maintenance("Archive", "secret-value") == ApprovalDecision(True)

    waiting = FakeBridgeApp(resolve_approval=False)
    bridge.bind(cast(Any, waiting))
    decisions: list[ApprovalDecision] = []
    thread = Thread(target=lambda: decisions.append(bridge.request("echo", "safe", "workspace")))
    thread.start()
    assert waiting.approval_requested.wait(timeout=1)
    bridge.reject_pending()
    thread.join(timeout=1)
    assert decisions == [ApprovalDecision(False)]
