"""Provider-neutral values used by read-only web research capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

WebCategory = Literal["general", "news"]
WebTimeRange = Literal["day", "month", "year"]


@dataclass(frozen=True, slots=True)
class WebSearchRequest:
    """Validated query submitted to a web-search provider."""

    query: str
    category: WebCategory = "general"
    language: str = "en"
    time_range: WebTimeRange | None = None
    max_results: int = 8
    page: int = 1


@dataclass(frozen=True, slots=True)
class WebSource:
    """One normalized and ranked web-search result."""

    source_id: str
    title: str
    url: str
    domain: str
    snippet: str
    published_at: str | None
    engines: tuple[str, ...]
    score: float
    rank: int


@dataclass(frozen=True, slots=True)
class WebSearchResponse:
    """Normalized provider response with recoverable warnings."""

    sources: tuple[WebSource, ...]
    warnings: tuple[str, ...] = ()
    has_next_page: bool = False


@dataclass(frozen=True, slots=True)
class FetchedWebPage:
    """One safely downloaded and locally extracted public web page."""

    source_id: str
    requested_url: str
    final_url: str
    title: str
    author: str | None
    published_at: str | None
    fetched_at: str
    content: str
    content_sha256: str
    truncated: bool = False
