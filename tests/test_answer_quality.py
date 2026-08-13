"""Tests for shared answer normalization and source-quality checks."""

from local_harness.application.answer_quality import (
    AnswerQualityPolicy,
    normalize_assistant_markdown,
)
from local_harness.application.timeline import build_request_timeline
from local_harness.domain.models import Message, ProgressEvent


def test_markdown_normalization_preserves_code_and_repairs_breaks() -> None:
    """Legacy HTML breaks are repaired outside fenced code only."""
    value = "A<br>B\n\n\n\n\n```html\nA<br>B\n```"

    normalized = normalize_assistant_markdown(value)

    assert "A\n\nB" in normalized
    assert "```html\nA<br>B\n```" in normalized
    assert "\n\n\n\n" not in normalized


def test_web_quality_requires_exact_successfully_read_sources() -> None:
    """Web answers cite every read page and reject invented citation URLs."""
    messages = [
        Message(
            role="tool",
            name="read_web_pages",
            request_number=1,
            content=(
                '{"items":[{"final_url":"https://docs.example/a"},'
                '{"final_url":"https://docs.example/b"}]}'
            ),
        )
    ]
    policy = AnswerQualityPolicy()

    bad = policy.assess(
        "Fact [A](https://docs.example/a) and [wrong](https://wrong.example).", messages, 1
    )
    good = policy.assess(
        "Facts from [A](https://docs.example/a) and [B](https://docs.example/b).",
        messages,
        1,
    )

    assert {issue.code for issue in bad.issues} == {
        "unknown_citation",
        "missing_citations",
    }
    assert good.acceptable


def test_timeline_merges_model_request_with_tool_result() -> None:
    """One model request and matching tool outcome become one concise step."""
    events = [
        ProgressEvent(1, 2, "model_start", "Waiting", "model", "started", request_number=1),
        ProgressEvent(
            2, 2, "model_complete", "Read files", "read_files", "success", 900, request_number=1
        ),
        ProgressEvent(
            3, 2, "tool_complete", "Read files", "read_files", "success", 100, request_number=1
        ),
    ]

    steps = build_request_timeline(events, 1)

    assert len(steps) == 1
    assert steps[0].target == "read_files"
    assert steps[0].duration_ms == 1_000
