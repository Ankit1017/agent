# ADR 0013: Local SearXNG Metasearch

## Status

Accepted.

## Context

The harness needs current web discovery without a paid provider key or provider-specific application
logic. Search still necessarily communicates with external indexes.

## Decision

Run pinned SearXNG in the existing Docker stack on `127.0.0.1:8080`, enable JSON responses, and use
the no-key Brave, DuckDuckGo, and Bing engines. Access it through `WebSearchProvider`; keep Open
WebUI web search disabled. Normal startup never pulls images.

## Consequences

Search orchestration and configuration stay local and replaceable. Upstream engines still receive
queries and can rate-limit, block, or vary their results.
