"""Tests for model-authored progress summary helpers."""

from typing import cast

from local_harness.application.progress import (
    add_step_summary_schema,
    extract_final_summary,
    normalize_summary,
)
from local_harness.domain.models import ToolDefinition


def test_tool_schema_requires_orchestration_summary_without_mutation() -> None:
    """Tool schemas gain summary metadata while original definitions remain unchanged."""
    original = ToolDefinition(
        "inspect",
        "Inspect",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    augmented = add_step_summary_schema(original)

    augmented_properties = cast(dict[str, object], augmented.parameters["properties"])
    original_properties = cast(dict[str, object], original.parameters["properties"])
    assert "step_summary" in augmented_properties
    assert augmented.parameters["required"] == ["path", "step_summary"]
    assert "step_summary" not in original_properties


def test_summaries_are_normalized_bounded_and_extracted() -> None:
    """Untrusted summary text becomes one bounded line and is removed from answers."""
    long_summary = "one two three four five six seven eight nine ten eleven twelve thirteen\nsecret"
    assert normalize_summary(long_summary, "fallback").split()[-1] == "twelve"
    assert normalize_summary(None, "fallback") == "fallback"

    summary, answer = extract_final_summary(
        "<step_summary>  Explaining architecture now  </step_summary>\n\nFinal answer"
    )
    assert summary == "Explaining architecture now"
    assert answer == "Final answer"
    assert extract_final_summary("Plain answer") == ("Completed response", "Plain answer")
    assert extract_final_summary(None) == ("Completed response", "")
