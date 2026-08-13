"""Tests for shared LLM-call limit bounds."""

import pytest

from local_harness.domain.limits import validate_max_turns


@pytest.mark.parametrize("value", [1, 20, 100])
def test_validate_max_turns_accepts_bounds(value: int) -> None:
    """Inclusive configured bounds are valid."""
    assert validate_max_turns(value) == value


@pytest.mark.parametrize("value", [0, 101, True, "20"])
def test_validate_max_turns_rejects_invalid_values(value: object) -> None:
    """Out-of-range and non-integer limits are rejected."""
    with pytest.raises(ValueError):
        validate_max_turns(value)
