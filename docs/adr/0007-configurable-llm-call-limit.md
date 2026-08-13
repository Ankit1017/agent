# ADR 0007: Configurable Per-Request LLM-Call Limit

## Status

Accepted.

## Context

The original fixed default of eight was easy to confuse with a session-wide cap, and editing
`.env.example` or an already-running process did not change the active harness.

## Decision

Default to 20 LLM calls per request, validate 1–100, and support environment, CLI, saved-session, and
runtime configuration. Resolve precedence as CLI, saved session, environment, then default. Persist
CLI/runtime overrides in session schema v3 and display the effective value and source at startup.

## Consequences

Users can tune long tasks without code changes or repeated restarts. Existing v1/v2 sessions migrate
with no override. Higher limits can increase latency and resource use, while the hard ceiling prevents
unbounded loops.
