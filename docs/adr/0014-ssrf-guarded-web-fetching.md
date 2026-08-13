# ADR 0014: SSRF-Guarded Web Fetching

## Status

Accepted.

## Context

Reading arbitrary model-selected URLs can expose local services, metadata endpoints, credentials,
large downloads, and redirect-based policy bypasses.

## Decision

Use a dedicated synchronous fetcher that allows public HTTP(S) ports 80/443 only, validates every
DNS answer and redirect, disables environment proxies/cookies/authentication, respects robots.txt,
streams at most 2 MB, and accepts HTML/XHTML/plain text. Extract content locally with Trafilatura.

## Consequences

Common public pages are readable without browser execution. Private services, PDFs, attachments,
JavaScript-only pages, unusual ports, and disallowed robots paths are unavailable. DNS rebinding
between validation and connection remains documented residual risk.
