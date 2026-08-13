"""Identifiers used by persisted harness entities."""

from uuid import uuid4


def new_session_id() -> str:
    """Return a compact, filesystem-safe session identifier."""
    return uuid4().hex
