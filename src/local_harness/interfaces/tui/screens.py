"""Modal screens used by the full-screen terminal interface."""

from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Markdown, Static

from local_harness.domain.models import ApprovalDecision, ProgressEvent, Session


class ApprovalScreen(ModalScreen[ApprovalDecision]):
    """Require an explicit choice before an exact PowerShell command runs."""

    BINDINGS = [
        Binding("escape", "reject", "Reject"),
        Binding("n", "reject", "Reject"),
        Binding("y", "approve", "Approve"),
    ]

    def __init__(self, command: str, explanation: str, workspace: str) -> None:
        """Store already-redacted approval details for display."""
        super().__init__()
        self.command = command
        self.explanation = explanation
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        """Compose the approval warning and controls."""
        with Vertical(id="approval-dialog"):
            yield Label("PowerShell approval required", classes="modal-title")
            yield Static(f"Reason: {self.explanation}", markup=False)
            yield Static(f"Workspace: {self.workspace}", markup=False)
            yield Static(self.command, id="approval-command", markup=False)
            yield Static(
                "WARNING: Approval is the security boundary; PowerShell is not OS-sandboxed.",
                classes="warning",
            )
            yield Input(placeholder="Optional rejection feedback", id="rejection-feedback")
            with Horizontal(classes="modal-buttons"):
                yield Button("Reject", id="reject", variant="error")
                yield Button("Approve", id="approve", variant="success")

    def on_mount(self) -> None:
        """Focus rejection so the safe choice is the default."""
        self.query_one("#reject", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Resolve the modal from an explicit button press."""
        if event.button.id == "approve":
            self.action_approve()
        else:
            self.action_reject()

    def action_approve(self) -> None:
        """Approve the displayed exact command."""
        self.dismiss(ApprovalDecision(True))

    def action_reject(self) -> None:
        """Reject with optional user feedback."""
        feedback = self.query_one("#rejection-feedback", Input).value.strip()
        self.dismiss(ApprovalDecision(False, feedback))


class PatchApprovalScreen(ModalScreen[ApprovalDecision]):
    """Require explicit approval for one exact redacted workspace diff."""

    BINDINGS = [
        Binding("escape", "reject", "Reject"),
        Binding("n", "reject", "Reject"),
        Binding("y", "approve", "Approve"),
    ]

    def __init__(self, preview: str, explanation: str, workspace: str) -> None:
        """Store the already-redacted patch approval details."""
        super().__init__()
        self.preview = preview
        self.explanation = explanation
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        """Compose a scrollable diff and safe approval controls."""
        with Vertical(id="patch-approval-dialog"):
            yield Label("File patch approval required", classes="modal-title")
            yield Static(f"Reason: {self.explanation}", markup=False)
            yield Static(f"Workspace: {self.workspace}", markup=False)
            yield Static(self.preview, id="patch-diff", markup=False)
            yield Static(
                "WARNING: Approving modifies the exact workspace files shown above.",
                classes="warning",
            )
            yield Input(placeholder="Optional rejection feedback", id="patch-feedback")
            with Horizontal(classes="modal-buttons"):
                yield Button("Reject", id="patch-reject", variant="error")
                yield Button("Approve", id="patch-approve", variant="success")

    def on_mount(self) -> None:
        """Focus rejection so the safe choice remains the default."""
        self.query_one("#patch-reject", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Resolve the modal from an explicit button press."""
        if event.button.id == "patch-approve":
            self.action_approve()
        else:
            self.action_reject()

    def action_approve(self) -> None:
        """Approve the displayed exact patch."""
        self.dismiss(ApprovalDecision(True))

    def action_reject(self) -> None:
        """Reject the patch with optional user feedback."""
        feedback = self.query_one("#patch-feedback", Input).value.strip()
        self.dismiss(ApprovalDecision(False, feedback))


class MaintenanceApprovalScreen(ModalScreen[ApprovalDecision]):
    """Require explicit confirmation for recoverable session maintenance."""

    BINDINGS = [
        Binding("escape", "reject", "Reject"),
        Binding("n", "reject", "Reject"),
        Binding("y", "approve", "Approve"),
    ]

    def __init__(self, action: str, details: str) -> None:
        """Store already-redacted maintenance details."""
        super().__init__()
        self.action = action
        self.details = details

    def compose(self) -> ComposeResult:
        """Compose a default-reject maintenance confirmation."""
        with Vertical(id="approval-dialog"):
            yield Label(self.action, classes="modal-title")
            yield Static(self.details, id="approval-command", markup=False)
            yield Static(
                "This changes protected session storage but remains recoverable.",
                classes="warning",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Reject", id="reject", variant="error")
                yield Button("Continue", id="approve", variant="success")

    def on_mount(self) -> None:
        """Focus rejection by default."""
        self.query_one("#reject", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Resolve an explicit button choice."""
        self.dismiss(ApprovalDecision(event.button.id == "approve"))

    def action_approve(self) -> None:
        """Approve the displayed maintenance operation."""
        self.dismiss(ApprovalDecision(True))

    def action_reject(self) -> None:
        """Reject the maintenance operation."""
        self.dismiss(ApprovalDecision(False))


class SessionsScreen(ModalScreen[str | None]):
    """Search and select a saved session."""

    BINDINGS = [Binding("escape", "dismiss_none", "Close")]

    def __init__(self, sessions: list[Session]) -> None:
        """Create a picker over a stable session snapshot."""
        super().__init__()
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        """Compose search input and session table."""
        with Vertical(classes="table-dialog"):
            yield Label("Saved sessions", classes="modal-title")
            yield Input(placeholder="Filter sessions", id="session-filter")
            yield DataTable(id="session-table", cursor_type="row")

    def on_mount(self) -> None:
        """Populate the session table and focus search."""
        table = self.query_one(DataTable)
        table.add_columns("Session", "Updated", "Preview", "Model", "Calls")
        self._populate("")
        self.query_one(Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter sessions by ID, preview, or model."""
        self._populate(event.value.casefold())

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Resume the selected session."""
        self.dismiss(str(event.row_key.value))

    def action_dismiss_none(self) -> None:
        """Close without changing sessions."""
        self.dismiss(None)

    def _populate(self, query: str) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for session in self._sessions:
            preview = next(
                (
                    message.content or ""
                    for message in session.messages
                    if message.role == "user" and message.content
                ),
                "(empty)",
            )
            searchable = f"{session.session_id} {preview} {session.model}".casefold()
            if query and query not in searchable:
                continue
            calls = session.max_turns_override if session.max_turns_override is not None else "—"
            table.add_row(
                session.session_id,
                session.updated_at[:19],
                preview[:50],
                session.model,
                str(calls),
                key=session.session_id,
            )


class EventsScreen(ModalScreen[None]):
    """Display persisted model and tool lifecycle events."""

    BINDINGS = [Binding("escape", "dismiss_screen", "Close")]

    def __init__(self, events: list[ProgressEvent]) -> None:
        """Create an event viewer for the supplied event window."""
        super().__init__()
        self._events = events

    def compose(self) -> ComposeResult:
        """Compose the event table."""
        with Vertical(classes="table-dialog"):
            yield Label("Activity history", classes="modal-title")
            yield Input(
                placeholder="Filter: model, tool, error, running, tagged, tag:name, or text",
                id="event-filter",
            )
            yield DataTable(id="event-table", cursor_type="row")

    def on_mount(self) -> None:
        """Populate the event table."""
        table = self.query_one(DataTable)
        table.add_columns(
            "Seq", "Time", "Call", "Kind", "Summary", "Target", "Status", "Tags", "Duration"
        )
        self._populate("")
        self.query_one("#event-filter", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter event rows without changing persisted history."""
        self._populate(event.value.casefold().strip())

    def _populate(self, query: str) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for event in self._events:
            searchable = " ".join(
                (event.kind, event.summary, event.target, event.status, *event.tags)
            ).casefold()
            selected = (
                not query
                or (query == "model" and event.kind.startswith("model_"))
                or (query == "tool" and event.kind.startswith("tool_"))
                or (query in {"error", "errors"} and event.status == "error")
                or (query in {"running", "started"} and event.status == "started")
                or (query == "tagged" and bool(event.tags))
                or (query.startswith("tag:") and query[4:] in event.tags)
                or query in searchable
            )
            if not selected:
                continue
            table.add_row(
                str(event.sequence),
                event.created_at,
                str(event.call_number),
                event.kind,
                event.summary,
                event.target,
                event.status,
                ", ".join(event.tags),
                f"{event.duration_ms / 1000:.1f}s",
            )

    def action_dismiss_screen(self) -> None:
        """Close the viewer."""
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """Display keyboard shortcuts and slash commands."""

    BINDINGS = [Binding("escape", "dismiss_screen", "Close")]

    def compose(self) -> ComposeResult:
        """Compose concise help content."""
        yield Markdown(
            """# Harness help

**Ctrl+Enter** send · **Ctrl+R** sessions · **Ctrl+E** events · **Ctrl+F** filter · **Ctrl+N** new
session · **Ctrl+Q** quit · **Escape** close/reject a dialog

Commands: `/new`, `/resume <id>`, `/sessions`, `/events [count]`,
`/session-info`, `/summarize`, `/quota`, `/tag`, `/tags`, `/export`, `/archive`,
`/archives`, `/restore`, `/session-check`, `/plugins`, `/tools`, `/workflows`, `/workflow`,
`/eval`, `/handoff`, `/candidate`,
`/plan`, `/index`, `/memory`,
`/max-turns`, `/models`, `/model [name|reset]`,
`/help`, `/exit`
""",
            classes="help-dialog",
        )

    def action_dismiss_screen(self) -> None:
        """Close the help screen."""
        self.dismiss(None)


ApprovalCallback = Callable[[ApprovalDecision | None], None]
