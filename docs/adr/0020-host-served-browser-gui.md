# ADR 0020: Host-served browser GUI

## Status

Accepted.

## Decision

Serve React from FastAPI on Windows at `127.0.0.1:3000`. Keep LiteLLM, PostgreSQL, and SearXNG in
Docker, preserve terminal interfaces, and retain Open WebUI only as a manual profile on port 3001.

## Consequences

Approved PowerShell keeps Windows semantics. The controller manages a verified host PID and a
prebuilt static client; no browser framework enters core layers.
