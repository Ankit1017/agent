"""Tests for model-facing web research tools."""

from __future__ import annotations

import base64
import json

import pytest

from local_harness.domain.errors import ToolExecutionError
from local_harness.domain.web import (
    FetchedWebPage,
    WebSearchRequest,
    WebSearchResponse,
    WebSource,
)
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.web_cache import MemoryWebCache
from local_harness.infrastructure.web_tools import ReadWebPagesTool, WebSearchTool


class SearchProviderFake:
    """Return one deterministic source and track requests."""

    def __init__(self) -> None:
        """Create an empty request log."""
        self.requests: list[WebSearchRequest] = []

    def search(self, request: WebSearchRequest) -> WebSearchResponse:
        """Return a result containing a recognizable secret."""
        self.requests.append(request)
        source = WebSource(
            "web-1",
            "Official result",
            "https://example.com/",
            "example.com",
            "contains secret-value",
            None,
            ("brave",),
            1.0,
            1,
        )
        return WebSearchResponse((source,), ("bing: timeout",), True)


class PageFetcherFake:
    """Return one page and fail another for partial-batch testing."""

    def fetch(self, url: str) -> FetchedWebPage:
        """Return deterministic content or a translated failure."""
        if "fail" in url:
            raise ToolExecutionError("unavailable")
        return FetchedWebPage(
            "web-1",
            url,
            url,
            "Page",
            None,
            None,
            "2026-08-09T00:00:00+00:00",
            "content " * 500,
            "hash",
        )


def test_web_search_tool_validates_caches_redacts_and_paginates() -> None:
    """One search produces a bounded envelope and subsequent identical calls hit memory."""
    provider = SearchProviderFake()
    tool = WebSearchTool(
        provider,
        MemoryWebCache(),
        SecretRedactor(("secret-value",)),
        max_results=8,
        max_output_chars=8_000,
    )
    arguments = {"query": "current docs", "max_results": 3}

    first = json.loads(tool.execute(arguments).content)
    second = json.loads(tool.execute(arguments).content)

    assert len(provider.requests) == 1
    assert first["metadata"]["content_is_untrusted"] is True
    assert "secret-value" not in json.dumps(first)
    assert first["next_cursor"] == base64.urlsafe_b64encode(b"2").decode().rstrip("=")
    assert second["metadata"]["cache_hit"] is True


def test_web_search_treats_empty_cursor_as_first_page() -> None:
    """Local models may serialize an omitted initial cursor as an empty string."""
    provider = SearchProviderFake()
    tool = WebSearchTool(
        provider,
        MemoryWebCache(),
        SecretRedactor(),
        max_results=8,
        max_output_chars=8_000,
    )

    result = tool.execute({"query": "Python packaging", "cursor": ""})

    assert not result.is_error
    assert provider.requests[0].page == 1
    payload = json.loads(result.content)
    assert "cursor: empty -> first page" in payload["metadata"]["normalizations"]


@pytest.mark.parametrize("category", ["", "web", "search"])
def test_web_search_normalizes_general_category_aliases(category: str) -> None:
    """Common local-model category aliases map to SearXNG's general category."""
    provider = SearchProviderFake()
    tool = WebSearchTool(
        provider,
        MemoryWebCache(),
        SecretRedactor(),
        max_results=8,
        max_output_chars=8_000,
    )

    result = tool.execute({"query": "Python packaging", "category": category})

    assert not result.is_error
    assert provider.requests[0].category == "general"
    assert json.loads(result.content)["metadata"]["normalizations"]


@pytest.mark.parametrize("time_range", ["", "any", "all"])
def test_web_search_normalizes_unrestricted_time_aliases(time_range: str) -> None:
    """Common unrestricted time aliases map to an omitted provider filter."""
    provider = SearchProviderFake()
    tool = WebSearchTool(
        provider,
        MemoryWebCache(),
        SecretRedactor(),
        max_results=8,
        max_output_chars=8_000,
    )

    result = tool.execute({"query": "Python packaging", "time_range": time_range})

    assert not result.is_error
    assert provider.requests[0].time_range is None


def test_web_search_normalizes_exact_failed_session_arguments() -> None:
    """The noncanonical arguments observed in the failed live session now succeed."""
    provider = SearchProviderFake()
    tool = WebSearchTool(
        provider,
        MemoryWebCache(),
        SecretRedactor(),
        max_results=8,
        max_output_chars=8_000,
    )

    result = tool.execute(
        {
            "query": "Python 3.12 packaging recommendations",
            "max_results": 10,
            "category": "web",
            "cursor": "",
            "language": "en",
            "time_range": "any",
        }
    )

    assert not result.is_error
    assert provider.requests[0].max_results == 8
    assert provider.requests[0].category == "general"
    assert provider.requests[0].time_range is None
    normalizations = json.loads(result.content)["metadata"]["normalizations"]
    assert "max_results: 10 -> 8" in normalizations


def test_web_search_tool_rejects_invalid_inputs() -> None:
    """Queries, enums, counts, languages, and cursors have explicit bounds."""
    tool = WebSearchTool(
        SearchProviderFake(),
        MemoryWebCache(),
        SecretRedactor(),
        max_results=8,
        max_output_chars=8_000,
    )

    for arguments in (
        {},
        {"query": ""},
        {"query": "x", "category": "images"},
        {"query": "x", "time_range": "week"},
        {"query": "x", "language": ""},
        {"query": "x", "category": 1},
        {"query": "x", "time_range": 1},
        {"query": "x", "max_results": 0},
        {"query": "x", "max_results": -1},
        {"query": "x", "max_results": True},
        {"query": "x", "max_results": "8"},
        {"query": "x", "cursor": "bad"},
    ):
        assert tool.execute(arguments).is_error


def test_read_web_pages_returns_partial_bounded_results() -> None:
    """Batch reads preserve successes, failures, ordering, and shared truncation."""
    tool = ReadWebPagesTool(
        PageFetcherFake(),
        SecretRedactor(),
        max_pages=5,
        max_page_chars=2_000,
        max_total_chars=6_000,
    )

    result = tool.execute(
        {"urls": ["https://example.com/", "https://fail.example/", "https://example.com/"]}
    )
    payload = json.loads(result.content)

    assert not result.is_error
    assert payload["metadata"]["failures"] == 2
    assert [item["status"] for item in payload["items"]] == ["success", "error", "error"]
    assert payload["items"][0]["truncated"] is True


def test_read_web_pages_rejects_invalid_batches() -> None:
    """The public tool rejects malformed and completely failed batches."""
    tool = ReadWebPagesTool(
        PageFetcherFake(),
        SecretRedactor(),
        max_pages=2,
        max_page_chars=2_000,
        max_total_chars=6_000,
    )

    assert tool.execute({"urls": []}).is_error
    assert tool.execute({"urls": [1]}).is_error
    assert tool.execute({"urls": ["https://fail.example/"]}).is_error
