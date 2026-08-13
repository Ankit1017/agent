"""Opt-in smoke test for a developer's running LiteLLM gateway."""

from __future__ import annotations

import os

import pytest

from local_harness.domain.models import Message
from local_harness.infrastructure.openai_model import OpenAIModelClient


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("HARNESS_RUN_LIVE_TEST") != "1", reason="opt-in live test")
def test_live_gateway_returns_a_message() -> None:
    """The configured local gateway accepts the production adapter's request."""
    client = OpenAIModelClient(
        os.environ.get("OPENAI_BASE_URL", "http://localhost:4000/v1"),
        os.environ["OPENAI_API_KEY"],
        os.environ.get("OPENAI_MODEL", "gpt-oss:20b"),
    )
    response = client.complete([Message(role="user", content="Reply with OK")], [])
    assert response.role == "assistant"
