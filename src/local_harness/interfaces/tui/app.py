"""Full-screen Textual application for the local terminal harness."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Literal, cast

from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Footer, Label, LoadingIndicator, Markdown, RichLog, Static, TextArea

from local_harness.application.agent import AgentService
from local_harness.application.answer_quality import normalize_assistant_markdown
from local_harness.application.session_services import session_info
from local_harness.bootstrap import Runtime
from local_harness.domain.errors import HarnessError
from local_harness.domain.models import Message, ProgressEvent, Session
from local_harness.interfaces.commands import InterfaceCommand, parse_command
from local_harness.interfaces.tui.activity import RequestActivity
from local_harness.interfaces.tui.bridge import TuiBridge
from local_harness.interfaces.tui.screens import (
    ApprovalCallback,
    ApprovalScreen,
    EventsScreen,
    HelpScreen,
    MaintenanceApprovalScreen,
    PatchApprovalScreen,
    SessionsScreen,
)


class HarnessApp(App[None]):
    """Render conversations, activity, approvals, and session controls."""

    CSS = """
    Screen { background: $background; color: $text; }
    #status-header { height: 3; padding: 1 2; background: $surface; color: $text; }
    #body { height: 1fr; }
    #conversation { width: 3fr; padding: 1 2; scrollbar-color: #2f81f7; }
    #activity-panel { width: 36; border-left: solid $border; padding: 1; background: $surface; }
    #activity-title { text-style: bold; color: $text; margin-bottom: 1; }
    #activity-current { min-height: 4; }
    #activity-history { color: $text-muted; height: 1fr; background: $surface; }
    #busy-indicator { height: 1; width: 8; display: none; }
    #busy-indicator.busy { display: block; }
    .message-role { text-style: bold; margin-top: 1; color: $text; }
    .user-message { padding: 1 2; border-left: thick $primary; background: $surface; }
    .assistant-message { padding: 1 2; border-left: thick $success; }
    .request-activity {
        width: 100%; height: auto; margin: 1 0 0 0;
        padding: 0 1; border-left: thick $primary; background: $surface;
    }
    .request-activity.completed { border-left: thick #3fb950; }
    .request-activity.warning-state { border-left: thick #d29922; }
    .request-activity.failed { border-left: thick #f85149; }
    .request-activity-timeline { height: auto; padding: 0 1 1 2; color: $text-muted; }
    .system-message { color: $warning; padding: 0 1; }
    .error-message { color: $error; padding: 0 1; }
    #composer-area { height: 7; border-top: solid $border; padding: 0 1; }
    #composer { height: 5; }
    Footer { background: $surface; }
    ModalScreen { align: center middle; background: #000000 65%; }
    #approval-dialog, #patch-approval-dialog, .table-dialog, .help-dialog {
        width: 80%; max-width: 100; height: auto; max-height: 85%;
        padding: 1 2; border: round $primary; background: $surface;
    }
    .table-dialog { height: 80%; }
    .modal-title { text-style: bold; color: $text; margin-bottom: 1; }
    #approval-command { margin: 1 0; padding: 1; background: $background; color: $text; }
    #patch-approval-dialog { height: 90%; }
    #patch-diff {
        height: 1fr; margin: 1 0; padding: 1; overflow-y: auto;
        background: $background; color: $text;
    }
    .warning { color: #f2cc60; margin-bottom: 1; }
    .modal-buttons { height: 3; align-horizontal: right; margin-top: 1; }
    .modal-buttons Button { margin-left: 1; }
    #session-filter { margin-bottom: 1; }
    .narrow #activity-panel { display: none; }
    .narrow #conversation { width: 1fr; }
    """

    BINDINGS = [
        Binding("ctrl+enter", "submit_prompt", "Send", priority=True),
        Binding("ctrl+r", "sessions", "Sessions", priority=True),
        Binding("ctrl+e", "events", "Events", priority=True),
        Binding("ctrl+f", "filter_events", "Filter", priority=True),
        Binding("ctrl+h", "help", "Help", priority=True),
        Binding("ctrl+t", "toggle_theme", "Theme", priority=True),
        Binding("ctrl+n", "new_session", "New", priority=True),
        Binding("ctrl+q", "quit_harness", "Quit", priority=True),
    ]

    def __init__(
        self,
        runtime: Runtime,
        agent: AgentService,
        bridge: TuiBridge,
    ) -> None:
        """Create the app around an already-composed runtime and session agent."""
        super().__init__()
        self.runtime = runtime
        self.agent = agent
        self.bridge = bridge
        self._busy = False
        self._has_activity = False
        self._request_activities: dict[int, RequestActivity] = {}
        self._active_request_number: int | None = None

    def compose(self) -> ComposeResult:
        """Compose the balanced chat-and-activity layout."""
        yield Static(id="status-header", markup=False)
        with Horizontal(id="body"):
            yield ScrollableContainer(id="conversation")
            with Vertical(id="activity-panel"):
                yield Label("Activity", id="activity-title")
                yield LoadingIndicator(id="busy-indicator")
                yield Static("Idle", id="activity-current", markup=False)
                yield RichLog(id="activity-history", wrap=True, markup=False, auto_scroll=True)
        with Vertical(id="composer-area"):
            yield TextArea(id="composer", tab_behavior="indent")
        yield Footer()

    def on_mount(self) -> None:
        """Load recent transcript and activity after widgets are available."""
        self.bridge.bind(self)
        self._refresh_header()
        self._render_transcript(self.agent.session)
        if Path(self.agent.session.workspace).resolve() != self.runtime.workspace:
            self._add_notice(
                f"Warning: session was created in {self.agent.session.workspace}; "
                f"tools use {self.runtime.workspace}."
            )
        self._refresh_activity(self.agent.session.events)
        integrity_findings = getattr(self.runtime, "integrity_findings", [])
        if integrity_findings:
            self._add_notice(
                f"Warning: {len(integrity_findings)} session integrity issue(s); "
                "use /session-check."
            )
        self.query_one("#composer", TextArea).focus()

    def on_unmount(self) -> None:
        """Safely unblock a pending approval during shutdown."""
        self.bridge.reject_pending()

    def on_resize(self, event: events.Resize) -> None:
        """Collapse the activity sidebar on terminals narrower than 100 columns."""
        self.set_class(event.size.width < 100, "narrow")

    def action_submit_prompt(self) -> None:
        """Submit the multiline composer or route a slash command."""
        if self._busy:
            self.notify("A request is already running.", severity="warning")
            return
        composer = self.query_one("#composer", TextArea)
        value = composer.text.strip()
        if not value:
            return
        composer.clear()
        parsed = parse_command(value)
        if parsed.error:
            self._add_notice(parsed.error, error=True)
            return
        if parsed.command is not None:
            self._dispatch_command(parsed.command)
            return
        sanitizer = getattr(self.agent, "sanitize_input", lambda prompt: (prompt, False))
        safe_value, changed = sanitizer(value)
        if changed:
            self._add_notice("Credential-like text was redacted before sending.")
        request_number = self.agent.next_request_number
        self._add_message("You", safe_value, assistant=False)
        self._mount_request_activity(request_number, [])
        self._active_request_number = request_number
        self._set_busy(True)
        self._submit_to_agent(safe_value)

    @work(thread=True, exclusive=True, group="agent")
    def _submit_to_agent(self, value: str) -> None:
        """Run the synchronous agent away from Textual's UI thread."""
        try:
            response = self.agent.submit(value)
        except HarnessError as exc:
            self.call_from_thread(self._finish_request, None, str(exc))
        except Exception as exc:  # defensive boundary around the UI worker
            self.call_from_thread(self._finish_request, None, f"Unexpected error: {exc}")
        else:
            self.call_from_thread(self._finish_request, response, None)

    def _finish_request(self, response: str | None, error: str | None) -> None:
        if self._active_request_number is not None:
            activity = self._request_activities.get(self._active_request_number)
            if activity is not None:
                activity.finish(failed=error is not None)
        if error is not None:
            self._add_notice(f"Error: {error}", error=True)
        elif response is not None:
            self._add_message("Assistant", response, assistant=True)
        self._set_busy(False)
        self._active_request_number = None
        self.query_one("#composer", TextArea).focus()

    def show_progress(self, event: ProgressEvent) -> None:
        """Update current and recent activity for one progress event."""
        seconds = event.duration_ms / 1000
        if event.kind == "model_start":
            line = f"LLM #{event.call_number}\n{event.summary}…"
        else:
            marker = (
                "OK"
                if event.status == "success"
                else "WARNING"
                if event.status == "warning"
                else "ERROR"
            )
            line = (
                f"{event.kind.replace('_', ' ').title()} · {marker}\n"
                f"{event.summary}\n{event.target} · {seconds:.1f}s"
            )
        self.query_one("#activity-current", Static).update(line)
        if event.request_number is not None:
            activity = self._request_activities.get(event.request_number)
            if activity is not None:
                activity.record(event)
        history = self.query_one("#activity-history", RichLog)
        if not self._has_activity:
            history.clear()
        history.write(_format_activity_event(event))
        self._has_activity = True

    def request_approval(
        self,
        command: str,
        explanation: str,
        workspace: str,
        callback: ApprovalCallback,
    ) -> None:
        """Open a default-reject command approval modal."""
        self.push_screen(ApprovalScreen(command, explanation, workspace), callback)

    def request_patch_approval(
        self,
        preview: str,
        explanation: str,
        workspace: str,
        callback: ApprovalCallback,
    ) -> None:
        """Open a scrollable, default-reject exact patch approval modal."""
        self.push_screen(PatchApprovalScreen(preview, explanation, workspace), callback)

    def request_maintenance_approval(
        self, action: str, details: str, callback: ApprovalCallback
    ) -> None:
        """Open a default-reject session maintenance modal."""
        self.push_screen(MaintenanceApprovalScreen(action, details), callback)

    def action_sessions(self) -> None:
        """Open the searchable session picker."""
        if self._busy:
            self.notify("Wait for the current request to finish.", severity="warning")
            return
        self.push_screen(
            SessionsScreen(self.runtime.sessions.list_sessions()), self._resume_selected
        )

    def action_events(self) -> None:
        """Open the complete persisted activity history for this session."""
        self.push_screen(EventsScreen(self.agent.session.events))

    def action_filter_events(self) -> None:
        """Open the event viewer with its filter field focused."""
        self.action_events()

    def action_help(self) -> None:
        """Open the command and shortcut reference."""
        self.push_screen(HelpScreen())

    def action_toggle_theme(self) -> None:
        """Switch between Textual's accessible dark and light themes."""
        self.theme = "textual-light" if self.theme == "textual-dark" else "textual-dark"

    def action_new_session(self) -> None:
        """Start and display a new saved session when idle."""
        if self._busy:
            self.notify("Wait for the current request to finish.", severity="warning")
            return
        self._switch_session(self.runtime.new_session(), "Started new session")

    def action_quit_harness(self) -> None:
        """Exit when no model or tool operation is active."""
        if self._busy:
            self.notify("The current call must finish before exit.", severity="warning")
            return
        self.exit()

    def _dispatch_command(self, command: InterfaceCommand) -> None:
        if command.name == "help":
            self.action_help()
        elif command.name == "sessions":
            self.action_sessions()
        elif command.name == "events":
            self._open_events(command.argument)
        elif command.name == "new":
            self.action_new_session()
        elif command.name == "resume":
            self._resume_by_id(command.argument)
        elif command.name == "max-turns":
            self._configure_turns(command.argument)
        elif command.name == "models":
            self._add_notice(
                "\n".join(
                    ("* " if item == self.agent.session.model else "  ") + item
                    for item in self.runtime.settings.models
                )
            )
        elif command.name == "model":
            if not command.argument:
                self._add_notice(f"model={self.agent.session.model}")
            else:
                try:
                    self.agent = self.runtime.switch_model(
                        self.agent.session,
                        None if command.argument.casefold() == "reset" else command.argument,
                    )
                    self._add_notice(f"model={self.agent.session.model}")
                    self._refresh_header()
                except HarnessError as exc:
                    self._add_notice(f"Error: {exc}", error=True)
        elif command.name == "exit":
            self.action_quit_harness()
        elif command.name == "session-info":
            session = self._load_optional_session(command.argument)
            if session is not None:
                budget = session.token_budget_override or self.runtime.settings.session_token_budget
                self._add_notice(session_info(session, budget))
        elif command.name == "summarize":
            self._set_busy(True)
            self._summarize_command(command.argument)
        elif command.name == "quota":
            self._quota_command(command.argument)
        elif command.name == "tag":
            self._tag_command(command.argument)
        elif command.name == "tags":
            try:
                events = self.runtime.session_service.tagged_events(
                    self.agent.session, command.argument
                )
                self.push_screen(EventsScreen(events))
            except HarnessError as exc:
                self._add_notice(f"Error: {exc}", error=True)
        elif command.name == "export":
            self._export_command(command.argument)
        elif command.name in {"archive", "session-check"}:
            self._set_busy(True)
            self._maintenance_command(command)
        elif command.name == "archives":
            values = self.runtime.session_service.list_archives()
            self._add_notice(
                "\n".join(f"{item.session_id}  {item.archived_at}" for item in values)
                or "No archives."
            )
        elif command.name == "restore":
            try:
                restored = self.runtime.session_service.restore(command.argument)
                self._add_notice(f"Restored session {restored.session_id}")
            except HarnessError as exc:
                self._add_notice(f"Error: {exc}", error=True)
        elif command.name == "plugins":
            self._add_notice(
                "\n".join(
                    f"{item.name}  {item.state}  {', '.join(item.tools)}"
                    for item in self.runtime.plugin_statuses
                )
                or "No plugins discovered."
            )
        elif command.name == "tools":
            tool_entries = self.agent.tool_catalog(command.argument)
            self._add_notice(
                "\n".join(
                    f"{item.name} [{item.profile}/{item.risk}] {item.description}"
                    for item in tool_entries
                )
                or "No matching tools."
            )
        elif command.name == "workflows":
            entries = self.agent.workflow_catalog(command.argument)
            self._add_notice(
                "\n".join(
                    f"{item.workflow_id} — {item.title}: {item.description}" for item in entries
                )
                or "No matching workflows."
            )
        elif command.name == "workflow":
            parts = command.argument.split()
            try:
                if parts == ["auto"]:
                    self.agent.configure_workflow(None)
                    self._add_notice("Next request will use automatic workflow selection.")
                elif len(parts) == 2 and parts[0] == "use":
                    self.agent.configure_workflow(parts[1])
                    self._add_notice(f"Next request will use workflow {parts[1]}.")
                elif not parts or parts == ["status"]:
                    run = self.agent.workflow_status()
                    pending = self.agent.session.pending_workflow_override or "auto"
                    lines = [f"Next workflow: {pending}"]
                    if run is not None:
                        lines.append(f"Last: {run.workflow_id} [{run.status}]")
                        lines.extend(
                            f"- [{stage.status}] {stage.description}" for stage in run.stages
                        )
                    self._add_notice("\n".join(lines))
                else:
                    self._add_notice("Usage: /workflow [status|auto|use <id>]", error=True)
            except HarnessError as exc:
                self._add_notice(f"Error: {exc}", error=True)
        elif command.name == "plan":
            plan = self.agent.session.plans[-1] if self.agent.session.plans else None
            if plan is None:
                self._add_notice("No task plan in this session.")
            else:
                lines = [f"Plan: {plan.goal} [{plan.status}]"]
                lines.extend(
                    f"{step.step_id}. [{step.status}] {step.description}"
                    + (f" — {step.result}" if step.result else "")
                    for step in plan.steps
                )
                self._add_notice("\n".join(lines))
        elif command.name in {"eval", "handoff", "candidate"}:
            self._set_busy(True)
            self._evaluation_command(command)
        elif command.name in {"index", "memory"}:
            self._set_busy(True)
            self._project_memory_command(command)

    @work(thread=True, exclusive=True, group="evaluation")
    def _evaluation_command(self, command: InterfaceCommand) -> None:
        """Run evaluation persistence or proposal calls away from the UI thread."""
        try:
            evaluation = self.runtime.evaluation
            if evaluation is None:
                self.call_from_thread(self._add_notice, "Evaluation is disabled.", True)
                return
            if command.name == "handoff":
                handoff = evaluation.handoff(self.agent.session.session_id)
                text = json.dumps(asdict(handoff), indent=2) if handoff else "No handoff snapshot."
            elif command.name == "eval":
                parts = command.argument.split()
                action = parts[0] if parts else "status"
                if action == "status":
                    text = json.dumps(evaluation.status(), indent=2)
                elif action == "contract":
                    number = int(parts[1]) if len(parts) > 1 else self.agent.next_request_number - 1
                    contract = evaluation.contract(self.agent.session.session_id, number)
                    text = json.dumps(asdict(contract), indent=2) if contract else "No contract."
                elif action == "mark" and len(parts) >= 2:
                    observation = evaluation.mark(
                        self.agent.session.session_id,
                        self.agent.next_request_number - 1,
                        cast(Literal["pass", "fail"], parts[1]),
                        " ".join(parts[2:]),
                    )
                    text = (
                        f"Marked request {observation.request_number} as {observation.user_mark}."
                    )
                elif action == "history":
                    values = evaluation.history(int(parts[1]) if len(parts) > 1 else 20)
                    text = (
                        "\n".join(
                            f"{item.observation_id} [{item.score.outcome}] "
                            f"request {item.request_number}"
                            for item in values
                        )
                        or "No evaluation history."
                    )
                elif action == "compare" and len(parts) == 3:
                    text = json.dumps(asdict(evaluation.compare(parts[1], parts[2])), indent=2)
                elif action == "run":
                    suite = next((item for item in parts[1:] if not item.startswith("--")), "core")
                    text = json.dumps(
                        asdict(evaluation.run_suite(suite, live="--live" in parts)), indent=2
                    )
                else:
                    raise ValueError("Invalid /eval command")
            else:
                parts = command.argument.split(maxsplit=2)
                if parts and parts[0] == "propose":
                    candidate = evaluation.propose(
                        self.runtime.model_client_for(self.agent.session.model),
                        parts[1] if len(parts) > 1 else "",
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
                text = json.dumps(asdict(candidate), indent=2)
            self.call_from_thread(self._add_notice, text)
        except (HarnessError, ValueError) as exc:
            self.call_from_thread(self._add_notice, f"Error: {exc}", True)
        finally:
            self.call_from_thread(self._set_busy, False)

    def _open_events(self, argument: str) -> None:
        if not argument:
            self.push_screen(EventsScreen(self.agent.session.events))
            return
        try:
            count = int(argument)
            if count <= 0:
                raise ValueError
        except ValueError:
            self._add_notice("Usage: /events [positive-count]", error=True)
            return
        self.push_screen(EventsScreen(self.agent.session.events[-count:]))

    @work(thread=True, exclusive=True, group="maintenance")
    def _project_memory_command(self, command: InterfaceCommand) -> None:
        """Run index refresh and embedding retrieval away from the UI thread."""
        try:
            if command.name == "memory":
                message = self.agent.query_project_memory(command.argument).rendered
            else:
                if command.argument not in {"", "refresh", "rebuild"}:
                    raise ValueError("Usage: /index [refresh|rebuild]")
                status = (
                    self.agent.refresh_project_index(rebuild=command.argument == "rebuild")
                    if command.argument
                    else self.agent.project_index_status()
                )
                message = (
                    "Project memory is disabled."
                    if status is None
                    else (
                        f"Index generation={status.generation}; files={status.files}; "
                        f"symbols={status.symbols}; dependencies={status.dependencies}; "
                        f"mode={status.retrieval_mode}; stale={status.stale}"
                    )
                )
            self.call_from_thread(self._add_notice, message)
        except (HarnessError, ValueError) as exc:
            self.call_from_thread(self._add_notice, f"Error: {exc}", error=True)
        finally:
            self.call_from_thread(self._set_busy, False)

    def _configure_turns(self, argument: str) -> None:
        if not argument:
            self._add_notice(
                f"max LLM calls/request={self.agent.max_turns} "
                f"(source={self.agent.max_turns_source})"
            )
            return
        try:
            value = None if argument.casefold() == "reset" else int(argument)
            self.agent.configure_max_turns(value)
        except ValueError as exc:
            self._add_notice(f"Error: {exc}", error=True)
            return
        self._refresh_header()
        self._add_notice(
            f"max LLM calls/request={self.agent.max_turns} (source={self.agent.max_turns_source})"
        )

    def _quota_command(self, argument: str) -> None:
        try:
            if argument.casefold() == "reset":
                self.agent.configure_token_budget(None)
            elif argument:
                self.agent.configure_token_budget(int(argument))
            budget = self.agent.token_budget or "disabled"
            self._refresh_header()
            self._add_notice(f"session tokens={self.agent.token_usage}, advisory budget={budget}")
        except ValueError as exc:
            self._add_notice(f"Error: {exc}", error=True)

    def _tag_command(self, argument: str) -> None:
        try:
            action, sequence, label = argument.split(maxsplit=2)
            if action == "add":
                self.runtime.session_service.add_tag(self.agent.session, int(sequence), label)
            elif action == "remove":
                self.runtime.session_service.remove_tag(self.agent.session, int(sequence), label)
            else:
                raise ValueError("Use add or remove")
            self._refresh_activity(self.agent.session.events)
            self._add_notice(f"Tag {action} completed.")
        except (ValueError, HarnessError) as exc:
            self._add_notice(f"Error: {exc}", error=True)

    def _export_command(self, argument: str) -> None:
        parts = argument.split()
        try:
            session = self._load_optional_session(parts[1] if len(parts) > 1 else "")
            if session is not None:
                result = self.runtime.session_service.export(session, parts[0])
                self._add_notice(f"Exported: {result.path}")
        except (IndexError, HarnessError) as exc:
            self._add_notice(f"Error: {exc}", error=True)

    def _load_optional_session(self, session_id: str) -> Session | None:
        if not session_id:
            return self.agent.session
        try:
            return self.runtime.sessions.load(session_id)
        except HarnessError as exc:
            self._add_notice(f"Error: {exc}", error=True)
            return None

    @work(thread=True, exclusive=True, group="maintenance")
    def _summarize_command(self, session_id: str) -> None:
        """Generate an explicit session summary without freezing Textual."""
        try:
            session = (
                self.agent.session if not session_id else self.runtime.sessions.load(session_id)
            )
            target = self.agent if session is self.agent.session else self.runtime.agent(session)
            summary = target.summarize_with_model()
            self.call_from_thread(self._finish_auxiliary, f"Summary: {summary}", False)
        except HarnessError as exc:
            self.call_from_thread(self._finish_auxiliary, f"Error: {exc}", True)

    @work(thread=True, exclusive=True, group="maintenance")
    def _maintenance_command(self, command: InterfaceCommand) -> None:
        """Run approval-blocking archive or quarantine work off the UI thread."""
        try:
            if command.name == "archive":
                active = command.argument == self.agent.session.session_id
                info = self.runtime.session_service.archive(command.argument)
                self.call_from_thread(self._after_archive, info.session_id, active)
                return
            parts = command.argument.split()
            if len(parts) == 2 and parts[0] == "quarantine":
                path = self.runtime.session_service.quarantine(parts[1])
                self.call_from_thread(self._finish_auxiliary, f"Quarantined: {path}", False)
            elif not parts:
                findings = self.runtime.session_service.scan()
                text = (
                    "\n".join(
                        f"{item.check_id}  {item.filename}  {item.reason}" for item in findings
                    )
                    or "Sessions are healthy."
                )
                self.call_from_thread(self._finish_auxiliary, text, False)
            else:
                self.call_from_thread(
                    self._finish_auxiliary,
                    "Usage: /session-check [quarantine <check-id>]",
                    True,
                )
        except HarnessError as exc:
            self.call_from_thread(self._finish_auxiliary, f"Error: {exc}", True)

    def _after_archive(self, session_id: str, active: bool) -> None:
        self._set_busy(False)
        self._add_notice(f"Archived session {session_id}")
        if active:
            self._switch_session(self.runtime.new_session(), "Started new session")

    def _finish_auxiliary(self, message: str, error: bool) -> None:
        self._add_notice(message, error=error)
        self._set_busy(False)
        self.query_one("#composer", TextArea).focus()

    def _resume_by_id(self, session_id: str) -> None:
        try:
            session = self.runtime.sessions.load(session_id)
        except HarnessError as exc:
            self._add_notice(f"Error: {exc}", error=True)
            return
        self._switch_session(session, f"Resumed session {session.session_id}")

    def _resume_selected(self, session_id: str | None) -> None:
        if session_id is not None:
            self._resume_by_id(session_id)

    def _switch_session(self, session: Session, notice: str) -> None:
        self.agent = self.runtime.agent(session)
        conversation = self.query_one("#conversation", ScrollableContainer)
        conversation.remove_children()
        self._request_activities.clear()
        self._active_request_number = None
        self._render_transcript(session)
        self._refresh_header()
        self._refresh_activity(session.events)
        self._add_notice(notice)
        self.query_one("#composer", TextArea).focus()

    def _render_transcript(self, session: Session) -> None:
        events_by_request: dict[int, list[ProgressEvent]] = {}
        for event in session.events:
            if event.request_number is not None:
                events_by_request.setdefault(event.request_number, []).append(event)
        visible = [
            message
            for message in session.messages
            if message.role in {"user", "assistant"}
            and message.content
            and not (message.role == "assistant" and message.tool_calls)
        ][-100:]
        for message in visible:
            self._mount_message(message)
            request_number = message.request_number
            if message.role == "user" and request_number is not None:
                self._mount_request_activity(
                    request_number, events_by_request.get(request_number, [])
                )
            elif message.role == "assistant" and request_number is not None:
                activity = self._request_activities.get(request_number)
                if activity is not None:
                    activity.finish(failed=False)
        completed = {
            message.request_number
            for message in visible
            if message.role == "assistant" and message.request_number is not None
        }
        for request_number, activity in self._request_activities.items():
            if request_number in completed:
                continue
            request_events = events_by_request.get(request_number, [])
            if request_events:
                activity.finish(failed=True)
        if not visible:
            self._add_notice("Ready. Describe a task or type /help.")

    def _mount_message(self, message: Message) -> None:
        role = "Assistant" if message.role == "assistant" else "You"
        self._add_message(role, message.content or "", assistant=message.role == "assistant")

    def _mount_request_activity(
        self, request_number: int, events: list[ProgressEvent]
    ) -> RequestActivity:
        activity = RequestActivity(request_number, events)
        self._request_activities[request_number] = activity
        self.query_one("#conversation", ScrollableContainer).mount(activity)
        return activity

    def _add_message(self, role: str, content: str, *, assistant: bool) -> None:
        container = self.query_one("#conversation", ScrollableContainer)
        container.mount(Label(role, classes="message-role"))
        if assistant:
            container.mount(
                Markdown(
                    normalize_assistant_markdown(content),
                    classes="assistant-message",
                    open_links=False,
                )
            )
        else:
            container.mount(Static(content, classes="user-message", markup=False))
        container.scroll_end(animate=False)

    def _add_notice(self, content: str, *, error: bool = False) -> None:
        container = self.query_one("#conversation", ScrollableContainer)
        style = "error-message" if error else "system-message"
        container.mount(Static(content, classes=style, markup=False))
        container.scroll_end(animate=False)

    def _set_busy(self, value: bool) -> None:
        self._busy = value
        composer = self.query_one("#composer", TextArea)
        composer.disabled = value
        self.query_one("#busy-indicator", LoadingIndicator).set_class(value, "busy")
        self._refresh_header()

    def _refresh_header(self) -> None:
        workspace = _short_workspace(self.runtime.workspace)
        state = "BUSY" if self._busy else "READY"
        token_usage = getattr(self.agent, "token_usage", 0)
        token_budget = getattr(self.agent, "token_budget", 0)
        self.query_one("#status-header", Static).update(
            f"Local Terminal Harness · {state}\n"
            f"{self.agent.session.model} · {workspace} · "
            f"session {self.agent.session.session_id[:8]} · {self.agent.max_turns} calls/request · "
            f"{token_usage}/{token_budget or '∞'} tokens"
        )

    def _refresh_activity(self, events: list[ProgressEvent]) -> None:
        history = self.query_one("#activity-history", RichLog)
        history.clear()
        self._has_activity = bool(events)
        if not events:
            history.write("No activity yet")
            return
        for event in events:
            history.write(_format_activity_event(event))


def _short_workspace(workspace: Path, limit: int = 45) -> str:
    """Shorten a workspace from the left while retaining its useful tail."""
    value = str(workspace)
    return value if len(value) <= limit else f"…{value[-(limit - 1) :]}"


def _format_activity_event(event: ProgressEvent) -> str:
    """Format one complete, single-session activity record for the sidebar."""
    timestamp = event.created_at[11:19] if len(event.created_at) >= 19 else event.created_at
    duration = f" · {event.duration_ms / 1000:.1f}s" if event.kind != "model_start" else ""
    source = (
        "LLM"
        if event.kind.startswith(("model_", "summary_"))
        else "TOOL"
        if event.kind.startswith("tool_")
        else "SYSTEM"
    )
    tags = f" · tags={','.join(event.tags)}" if event.tags else ""
    tokens = (
        f" · tokens={event.input_tokens + event.output_tokens} ({event.usage_source})"
        if event.input_tokens or event.output_tokens
        else ""
    )
    return (
        f"{timestamp} · {source} #{event.call_number} · {event.status.upper()}"
        f"{duration}{tokens}{tags}\n"
        f"{event.summary} → {event.target}"
    )
