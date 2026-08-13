"""Tests for shared command parsing and UI-mode selection."""

from __future__ import annotations

from io import StringIO

from local_harness.interfaces.commands import parse_command
from local_harness.interfaces.ui_mode import select_ui_mode


class TtyStream(StringIO):
    """String stream with a configurable terminal capability."""

    def __init__(self, interactive: bool) -> None:
        """Create the stream with a deterministic ``isatty`` result."""
        super().__init__()
        self.interactive = interactive

    def isatty(self) -> bool:
        """Return the configured terminal state."""
        return self.interactive


def test_ui_mode_honors_overrides_and_terminal_capabilities() -> None:
    """Automatic mode falls back safely while explicit modes remain stable."""
    tty = TtyStream(True)
    redirected = TtyStream(False)

    assert select_ui_mode("tui", redirected, redirected, {}) == "tui"
    assert select_ui_mode("plain", tty, tty, {}) == "plain"
    assert select_ui_mode("auto", tty, tty, {}) == "tui"
    assert select_ui_mode("auto", redirected, tty, {}) == "plain"
    assert select_ui_mode("auto", tty, redirected, {}) == "plain"
    assert select_ui_mode("auto", tty, tty, {"TERM": "dumb"}) == "plain"
    assert select_ui_mode("auto", tty, tty, {"NO_COLOR": "1"}) == "plain"


def test_command_parser_recognizes_shared_commands_and_errors() -> None:
    """Parsing is pure and distinguishes prompts, commands, and invalid syntax."""
    assert parse_command("hello").command is None
    assert parse_command("/events 5").command == parse_command(" /events 5 ").command
    assert parse_command("/resume").error == "Usage: /resume <session-id>"
    assert "does not accept" in parse_command("/help now").error
    assert "Unknown" in parse_command("/missing").error
    assert parse_command("/index rebuild").command is not None
    assert parse_command("/memory authentication").command is not None
    assert parse_command("/model gpt-5.5").command is not None
    assert parse_command("/models").command is not None
    assert "does not accept" in parse_command("/models now").error
    assert "Usage" in parse_command("/memory").error
