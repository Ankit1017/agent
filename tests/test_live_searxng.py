"""Opt-in live smoke test for the locally managed SearXNG instance."""

from __future__ import annotations

import os

import pytest

from local_harness.domain.web import WebSearchRequest
from local_harness.infrastructure.searxng import SearxngSearchProvider


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("HARNESS_RUN_LIVE_WEB_TEST") != "1", reason="opt-in live web test"
)
def test_live_searxng_returns_sources() -> None:
    """The configured local JSON endpoint returns at least one normalized source."""
    provider = SearxngSearchProvider(
        os.environ.get("SEARXNG_BASE_URL", "http://127.0.0.1:8080"), timeout_seconds=15
    )
    response = provider.search(WebSearchRequest("Python official documentation", max_results=3))
    assert response.sources
