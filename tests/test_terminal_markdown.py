"""Tests for terminal Markdown presentation modes."""

from io import StringIO

from local_harness.interfaces.markdown import write_assistant_markdown


class TtyBuffer(StringIO):
    """In-memory stream reporting interactive terminal capability."""

    def isatty(self) -> bool:
        """Report interactive output."""
        return True


def test_redirected_markdown_contains_no_terminal_control_sequences() -> None:
    """Automation receives normalized Markdown without ANSI styling."""
    stream = StringIO()

    write_assistant_markdown("# Result\n\nDone<br>Next", stream, {})

    assert "# Result" in stream.getvalue()
    assert "Done\n\nNext" in stream.getvalue()
    assert "\x1b" not in stream.getvalue()


def test_interactive_no_color_renders_readable_markdown() -> None:
    """NO_COLOR retains Rich layout without emitting color escapes."""
    stream = TtyBuffer()

    write_assistant_markdown("**Result**\n\n- one", stream, {"NO_COLOR": "1"})

    assert "Result" in stream.getvalue()
    assert "one" in stream.getvalue()
