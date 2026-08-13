"""SSRF-guarded webpage download and local main-content extraction."""

from __future__ import annotations

import hashlib
import socket
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from trafilatura import extract, extract_metadata

from local_harness.application.ports import WebPageFetcher
from local_harness.domain.errors import PolicyViolation, ToolExecutionError
from local_harness.domain.web import FetchedWebPage
from local_harness.guardrails.web_url_policy import PublicWebUrlPolicy
from local_harness.infrastructure.searxng import source_id
from local_harness.infrastructure.web_cache import MemoryWebCache
from local_harness.text import truncate_text

_USER_AGENT = "LocalTerminalHarness/0.1 (local read-only research tool)"
_PAGE_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class _Download:
    final_url: str
    content_type: str
    body: bytes


class SafeWebPageFetcher(WebPageFetcher):
    """Fetch public text pages with DNS, redirect, robots, and size controls."""

    def __init__(
        self,
        *,
        timeout_seconds: int,
        max_page_chars: int,
        cache: MemoryWebCache,
        client: httpx.Client | None = None,
        resolver: Callable[[str], Sequence[str]] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        max_download_bytes: int = 2_000_000,
    ) -> None:
        """Configure bounded networking with injectable deterministic seams."""
        self._max_page_chars = max_page_chars
        self._max_download_bytes = max_download_bytes
        self._cache = cache
        self._policy = PublicWebUrlPolicy()
        self._resolver = resolver or _resolve_addresses
        self._sleeper = sleeper
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,text/plain;q=0.9"},
        )
        self._robots: dict[str, RobotFileParser | None] = {}

    def fetch(self, url: str) -> FetchedWebPage:
        """Download, extract, hash, and bound one public page."""
        normalized = self._policy.normalize(url)
        cached = self._cache.get_page(normalized)
        if cached is not None:
            return cached
        if not self._robots_allowed(normalized):
            raise PolicyViolation("Website robots.txt disallows this page")
        downloaded = self._download(normalized, _PAGE_TYPES)
        decoded = downloaded.body.decode("utf-8", errors="replace")
        if downloaded.content_type == "text/plain":
            content = decoded.strip()
            title = urlsplit(downloaded.final_url).path.rsplit("/", 1)[-1] or "Plain text page"
            author = None
            published_at = None
        else:
            content = (
                extract(
                    decoded,
                    url=downloaded.final_url,
                    output_format="markdown",
                    include_links=False,
                    include_images=False,
                    favor_precision=True,
                )
                or ""
            ).strip()
            metadata = extract_metadata(decoded, default_url=downloaded.final_url)
            title = str(getattr(metadata, "title", "") or "Untitled page")[:300]
            author = _optional_metadata(metadata, "author")
            published_at = _optional_metadata(metadata, "date")
        if not content:
            raise ToolExecutionError("Web page contained no extractable text")
        bounded, truncated = truncate_text(content, self._max_page_chars)
        page = FetchedWebPage(
            source_id=source_id(downloaded.final_url),
            requested_url=normalized,
            final_url=downloaded.final_url,
            title=title,
            author=author,
            published_at=published_at,
            fetched_at=datetime.now(UTC).isoformat(),
            content=bounded,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            truncated=truncated,
        )
        self._cache.set_page(normalized, page)
        return page

    def clear_cache(self) -> None:
        """Clear robots state when the runtime switches sessions."""
        self._robots.clear()

    def _robots_allowed(self, url: str) -> bool:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            if len(self._robots) >= 128:
                del self._robots[next(iter(self._robots))]
            robots_url = f"{origin}/robots.txt"
            try:
                downloaded = self._download(robots_url, frozenset({"text/plain"}))
            except (PolicyViolation, ToolExecutionError):
                self._robots[origin] = None
            else:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(downloaded.body.decode("utf-8", errors="replace").splitlines())
                self._robots[origin] = parser
        cached_parser: RobotFileParser | None = self._robots[origin]
        return cached_parser is None or cached_parser.can_fetch(_USER_AGENT, url)

    def _download(self, url: str, allowed_types: frozenset[str]) -> _Download:
        current = url
        for redirect_count in range(4):
            current = self._policy.normalize(current)
            hostname = urlsplit(current).hostname
            if hostname is None:
                raise PolicyViolation("Web URL must contain a hostname")
            self._policy.validate_addresses(self._resolver(hostname))
            try:
                for attempt in range(2):
                    self._client.cookies.clear()
                    with self._client.stream("GET", current) as response:
                        if response.status_code in _RETRYABLE_STATUS_CODES and attempt == 0:
                            self._sleeper(_retry_delay(response.headers.get("retry-after")))
                            continue
                        result = self._consume_response(
                            response, current, redirect_count, allowed_types
                        )
                        if isinstance(result, str):
                            current = result
                            break
                        return result
                else:
                    raise ToolExecutionError("Web page retry attempts were exhausted")
                continue
            except httpx.HTTPError as exc:
                raise ToolExecutionError(f"Web page request failed: {exc}") from exc
            finally:
                self._client.cookies.clear()
        raise ToolExecutionError("Web page exceeded three redirects")

    def _consume_response(
        self,
        response: httpx.Response,
        current: str,
        redirect_count: int,
        allowed_types: frozenset[str],
    ) -> _Download | str:
        """Validate and consume one response, returning a redirect URL when present."""
        if response.status_code in _REDIRECT_CODES:
            location = response.headers.get("location")
            if not location:
                raise ToolExecutionError("Web redirect omitted its destination")
            if redirect_count == 3:
                raise ToolExecutionError("Web page exceeded three redirects")
            return cast(str, urljoin(current, location))
        response.raise_for_status()
        disposition = response.headers.get("content-disposition", "").casefold()
        if "attachment" in disposition:
            raise ToolExecutionError("Web downloads and attachments are not supported")
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        content_type = content_type.strip().casefold()
        if content_type not in allowed_types:
            raise ToolExecutionError(f"Unsupported web content type: {content_type or 'unknown'}")
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > self._max_download_bytes:
                raise ToolExecutionError("Web page exceeds the download byte limit")
        return _Download(current, content_type, bytes(body))


def _retry_delay(value: str | None) -> float:
    """Return a small bounded retry delay from an integer Retry-After header."""
    if value is None:
        return 0.25
    try:
        return min(2.0, max(0.0, float(value)))
    except ValueError:
        return 0.25


def _resolve_addresses(hostname: str) -> tuple[str, ...]:
    try:
        values = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ToolExecutionError(f"Web hostname resolution failed: {exc}") from exc
    return tuple(sorted({str(item[4][0]) for item in values}))


def _optional_metadata(metadata: object, name: str) -> str | None:
    value = getattr(metadata, name, None)
    return str(value)[:300] if value else None
