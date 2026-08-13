"""Local SearXNG adapter producing provider-neutral ranked sources."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

from local_harness.application.ports import WebSearchProvider
from local_harness.domain.errors import HarnessError, ToolExecutionError
from local_harness.domain.web import (
    WebSearchRequest,
    WebSearchResponse,
    WebSource,
)
from local_harness.guardrails.web_url_policy import PublicWebUrlPolicy


class SearxngSearchProvider(WebSearchProvider):
    """Query a loopback SearXNG JSON endpoint synchronously."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: int,
        client: httpx.Client | None = None,
    ) -> None:
        """Configure the local endpoint and an injectable HTTP client."""
        self._endpoint = f"{base_url.rstrip('/')}/search"
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )
        self._url_policy = PublicWebUrlPolicy()

    def search(self, request: WebSearchRequest) -> WebSearchResponse:
        """Return normalized, deduplicated, deterministically ranked results."""
        form: dict[str, str] = {
            "q": request.query,
            "categories": request.category,
            "language": request.language,
            "pageno": str(request.page),
            "format": "json",
            "safesearch": "1",
        }
        if request.time_range is not None:
            form["time_range"] = request.time_range
        try:
            response = self._client.post(self._endpoint, data=form)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolExecutionError(f"Local SearXNG request failed: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ToolExecutionError("Local SearXNG returned malformed JSON")
        sources = self._sources(payload["results"])
        warnings = _warnings(payload.get("unresponsive_engines"))
        selected = sources[: request.max_results]
        return WebSearchResponse(
            tuple(selected),
            warnings,
            has_next_page=len(sources) > request.max_results,
        )

    def _sources(self, raw_results: list[object]) -> list[WebSource]:
        deduplicated: dict[str, WebSource] = {}
        for rank, raw_result in enumerate(raw_results, start=1):
            if not isinstance(raw_result, Mapping):
                continue
            raw_url = raw_result.get("url")
            if not isinstance(raw_url, str):
                continue
            try:
                url = self._url_policy.normalize(raw_url)
            except HarnessError:
                continue
            engines = _engines(raw_result.get("engines"))
            score = _score(raw_result.get("score"))
            source = WebSource(
                source_id=_source_id(url),
                title=_text(raw_result.get("title"), "Untitled result", 300),
                url=url,
                domain=urlsplit(url).hostname or "",
                snippet=_text(raw_result.get("content"), "", 1_500),
                published_at=_optional_text(
                    raw_result.get("publishedDate", raw_result.get("published_date")),
                    100,
                ),
                engines=engines,
                score=score,
                rank=rank,
            )
            existing = deduplicated.get(url)
            if existing is None:
                deduplicated[url] = source
            else:
                deduplicated[url] = WebSource(
                    source_id=existing.source_id,
                    title=existing.title,
                    url=url,
                    domain=existing.domain,
                    snippet=existing.snippet or source.snippet,
                    published_at=existing.published_at or source.published_at,
                    engines=tuple(sorted(set(existing.engines) | set(source.engines))),
                    score=max(existing.score, source.score),
                    rank=min(existing.rank, source.rank),
                )
        return sorted(
            deduplicated.values(),
            key=lambda item: (-item.score, -len(item.engines), item.rank, item.url),
        )


def source_id(url: str) -> str:
    """Return the stable citation identifier used by both web tools."""
    return _source_id(url)


def _source_id(url: str) -> str:
    return f"web-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]}"


def _text(value: object, default: str, maximum: int) -> str:
    return value.strip()[:maximum] if isinstance(value, str) and value.strip() else default


def _optional_text(value: object, maximum: int) -> str | None:
    return value.strip()[:maximum] if isinstance(value, str) and value.strip() else None


def _engines(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(sorted({item[:100] for item in value if isinstance(item, str) and item}))


def _score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _warnings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    warnings: list[str] = []
    for item in value[:10]:
        if isinstance(item, list | tuple):
            warnings.append(": ".join(str(part)[:100] for part in item[:2]))
        elif isinstance(item, str):
            warnings.append(item[:200])
    return tuple(warnings)
