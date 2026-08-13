"""Shared bounds for configurable agent execution limits."""

DEFAULT_MAX_TURNS = 20
MIN_MAX_TURNS = 1
MAX_MAX_TURNS = 100


def validate_max_turns(value: object) -> int:
    """Return a valid per-request LLM-call limit or raise ``ValueError``."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("max turns must be an integer")
    if not MIN_MAX_TURNS <= value <= MAX_MAX_TURNS:
        raise ValueError(f"max turns must be between {MIN_MAX_TURNS} and {MAX_MAX_TURNS}")
    return value
