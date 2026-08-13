"""Offline tests for guarded downloading and local content extraction."""

from __future__ import annotations

import httpx
import pytest

from local_harness.domain.errors import PolicyViolation, ToolExecutionError
from local_harness.infrastructure.web_cache import MemoryWebCache
from local_harness.infrastructure.web_fetcher import SafeWebPageFetcher


def _fetcher(
    handler: httpx.MockTransport,
    *,
    resolver: object | None = None,
    max_bytes: int = 2_000_000,
) -> SafeWebPageFetcher:
    resolve = resolver if callable(resolver) else lambda _: ["8.8.8.8"]
    return SafeWebPageFetcher(
        timeout_seconds=2,
        max_page_chars=1_000,
        max_download_bytes=max_bytes,
        cache=MemoryWebCache(),
        client=httpx.Client(transport=handler, follow_redirects=False, trust_env=False),
        resolver=resolve,
    )


def test_fetcher_extracts_html_metadata_and_uses_cache() -> None:
    """HTML main content and metadata are extracted once and cached locally."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nAllow: /",
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(
            200,
            text=(
                "<html><head><title>Example</title><meta name='author' content='Ada'></head>"
                "<body><main><h1>Useful heading</h1>"
                "<p>Useful content here.</p></main></body></html>"
            ),
            headers={"content-type": "text/html; charset=utf-8"},
        )

    fetcher = _fetcher(httpx.MockTransport(handler))
    first = fetcher.fetch("https://example.com/page")
    second = fetcher.fetch("https://example.com/page")

    assert first is second
    assert first.title in {"Example", "Useful heading"}
    assert "Useful content" in first.content
    assert calls == ["/robots.txt", "/page"]


def test_fetcher_handles_redirect_and_plain_text() -> None:
    """Every redirect is revalidated and plain text remains readable."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "https://other.example/file.txt"})
        return httpx.Response(200, text="plain text", headers={"content-type": "text/plain"})

    fetcher = _fetcher(httpx.MockTransport(handler))
    page = fetcher.fetch("https://example.com/start")

    assert page.final_url == "https://other.example/file.txt"
    assert page.content == "plain text"


def test_fetcher_blocks_private_destinations_and_robots() -> None:
    """DNS and robots policy failures stop before page content is returned."""
    handler = httpx.MockTransport(lambda _: httpx.Response(500))
    private = _fetcher(handler, resolver=lambda _: ["127.0.0.1"])
    with pytest.raises(PolicyViolation, match="non-public"):
        private.fetch("https://example.com/")

    def robots_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="User-agent: *\nDisallow: /private",
            headers={"content-type": "text/plain"},
        )

    denied = _fetcher(httpx.MockTransport(robots_handler))
    with pytest.raises(PolicyViolation, match="robots"):
        denied.fetch("https://example.com/private")


@pytest.mark.parametrize(
    ("headers", "body", "message"),
    [
        ({"content-type": "application/pdf"}, b"pdf", "Unsupported"),
        (
            {"content-type": "text/html", "content-disposition": "attachment"},
            b"download",
            "attachments",
        ),
        ({"content-type": "text/plain"}, b"too long", "byte limit"),
    ],
)
def test_fetcher_rejects_content_boundaries(
    headers: dict[str, str], body: bytes, message: str
) -> None:
    """Unsupported, attached, and oversized responses fail safely."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers=headers, content=body)

    fetcher = _fetcher(httpx.MockTransport(handler), max_bytes=3)
    with pytest.raises(ToolExecutionError, match=message):
        fetcher.fetch("https://example.com/page")


def test_fetcher_rejects_empty_extraction() -> None:
    """Empty HTML does not masquerade as useful research context."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    with pytest.raises(ToolExecutionError, match="no extractable text"):
        _fetcher(httpx.MockTransport(handler)).fetch("https://example.com/")


def test_fetcher_retries_one_transient_response() -> None:
    """A bounded retry recovers from one upstream rate-limit response."""
    page_calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal page_calls
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        page_calls += 1
        if page_calls == 1:
            return httpx.Response(
                429,
                headers={"retry-after": "99", "content-type": "text/plain"},
            )
        return httpx.Response(
            200,
            text="official guidance",
            headers={"content-type": "text/plain"},
        )

    fetcher = SafeWebPageFetcher(
        timeout_seconds=2,
        max_page_chars=1_000,
        cache=MemoryWebCache(),
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False
        ),
        resolver=lambda _: ["8.8.8.8"],
        sleeper=delays.append,
    )

    assert fetcher.fetch("https://example.com/guide").content == "official guidance"
    assert page_calls == 2
    assert delays == [2.0]
