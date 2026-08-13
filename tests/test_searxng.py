"""Offline tests for local SearXNG response normalization."""

from __future__ import annotations

import httpx
import pytest

from local_harness.domain.errors import ToolExecutionError
from local_harness.domain.web import WebSearchRequest
from local_harness.infrastructure.searxng import SearxngSearchProvider


def _client(payload: object, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_searxng_maps_deduplicates_and_ranks_results() -> None:
    """Provider output is stable, canonical, deduplicated, and warning-aware."""
    payload = {
        "results": [
            {
                "url": "https://example.com/a#part",
                "title": "First",
                "content": "Snippet",
                "published_date": "2026-08-09",
                "engines": ["brave"],
                "score": 1.0,
            },
            {
                "url": "https://example.com/a",
                "title": "Duplicate",
                "engines": ["bing"],
                "score": 2.0,
            },
            {"url": "file:///bad", "title": "Unsafe"},
            {"url": "https://other.example/b", "title": "Other", "score": 0.5},
            "invalid",
        ],
        "unresponsive_engines": [["duckduckgo", "timeout"]],
    }
    provider = SearxngSearchProvider(
        "http://127.0.0.1:8080", timeout_seconds=2, client=_client(payload)
    )

    response = provider.search(WebSearchRequest("query", max_results=1))

    assert len(response.sources) == 1
    assert response.sources[0].engines == ("bing", "brave")
    assert response.sources[0].url == "https://example.com/a"
    assert response.sources[0].published_at == "2026-08-09"
    assert response.warnings == ("duckduckgo: timeout",)
    assert response.has_next_page


@pytest.mark.parametrize("payload", [{}, {"results": "bad"}, []])
def test_searxng_rejects_malformed_payloads(payload: object) -> None:
    """Malformed JSON shapes become translated tool errors."""
    provider = SearxngSearchProvider(
        "http://127.0.0.1:8080", timeout_seconds=2, client=_client(payload)
    )
    with pytest.raises(ToolExecutionError, match="malformed"):
        provider.search(WebSearchRequest("query"))


def test_searxng_translates_http_failures() -> None:
    """Local endpoint failures do not escape as HTTPX exceptions."""
    provider = SearxngSearchProvider(
        "http://127.0.0.1:8080", timeout_seconds=2, client=_client({}, 500)
    )
    with pytest.raises(ToolExecutionError, match="Local SearXNG request failed"):
        provider.search(WebSearchRequest("query"))
