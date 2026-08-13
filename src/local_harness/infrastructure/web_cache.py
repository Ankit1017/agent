"""Bounded in-memory cache for short-lived web research results."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from local_harness.domain.web import FetchedWebPage, WebSearchRequest, WebSearchResponse


@dataclass(frozen=True, slots=True)
class _CacheEntry[T]:
    value: T
    expires_at: float


class MemoryWebCache:
    """Cache immutable search/page values for one active session only."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 900,
        max_searches: int = 128,
        max_pages: int = 64,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        """Configure expiry, capacities, and a deterministic clock seam."""
        self._ttl_seconds = ttl_seconds
        self._max_searches = max_searches
        self._max_pages = max_pages
        self._clock = clock
        self._searches: OrderedDict[WebSearchRequest, _CacheEntry[WebSearchResponse]] = (
            OrderedDict()
        )
        self._pages: OrderedDict[str, _CacheEntry[FetchedWebPage]] = OrderedDict()

    def get_search(self, request: WebSearchRequest) -> WebSearchResponse | None:
        """Return and refresh one unexpired cached search."""
        return self._get(self._searches, request)

    def set_search(self, request: WebSearchRequest, response: WebSearchResponse) -> None:
        """Cache one normalized search response."""
        self._set(self._searches, request, response, self._max_searches)

    def get_page(self, url: str) -> FetchedWebPage | None:
        """Return and refresh one unexpired extracted page."""
        return self._get(self._pages, url)

    def set_page(self, url: str, page: FetchedWebPage) -> None:
        """Cache one extracted page by normalized requested URL."""
        self._set(self._pages, url, page, self._max_pages)

    def clear(self) -> None:
        """Discard all data when the active session changes."""
        self._searches.clear()
        self._pages.clear()

    def _get[K, V](self, cache: OrderedDict[K, _CacheEntry[V]], key: K) -> V | None:
        entry = cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            del cache[key]
            return None
        cache.move_to_end(key)
        return entry.value

    def _set[K, V](
        self, cache: OrderedDict[K, _CacheEntry[V]], key: K, value: V, maximum: int
    ) -> None:
        cache[key] = _CacheEntry(value, self._clock() + self._ttl_seconds)
        cache.move_to_end(key)
        while len(cache) > maximum:
            cache.popitem(last=False)
