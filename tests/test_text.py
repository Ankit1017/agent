"""Tests for focused text helpers."""

from local_harness.identifiers import new_session_id
from local_harness.text import truncate_text


def test_text_truncation_and_session_ids() -> None:
    """Text bounds report truncation and identifiers are storage-safe."""
    assert truncate_text("short", 10) == ("short", False)
    result, truncated = truncate_text("x" * 100, 50)
    assert truncated
    assert len(result) == 50
    identifier = new_session_id()
    assert len(identifier) == 32
    assert identifier.isalnum()
