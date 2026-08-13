"""Tests for session-scoped in-memory web caching."""

from __future__ import annotations

from local_harness.domain.web import WebSearchRequest, WebSearchResponse
from local_harness.infrastructure.web_cache import MemoryWebCache


def test_web_cache_expires_evicts_and_clears() -> None:
    """TTL, LRU capacity, and explicit session clearing are deterministic."""
    now = [0.0]
    cache = MemoryWebCache(ttl_seconds=10, max_searches=1, max_pages=1, clock=lambda: now[0])
    first = WebSearchRequest("first")
    second = WebSearchRequest("second")
    response = WebSearchResponse(())

    assert cache.get_search(first) is None
    cache.set_search(first, response)
    assert cache.get_search(first) is response
    cache.set_search(second, response)
    assert cache.get_search(first) is None
    now[0] = 11
    assert cache.get_search(second) is None
    cache.set_search(first, response)
    cache.clear()
    assert cache.get_search(first) is None
