# ADR 0016: Schema-v5 session analytics

## Status

Accepted.

## Decision

Persist bounded summaries, event tags, advisory quota overrides, and per-call token usage in schema
version 5. Load versions 1-4 with safe defaults. Prefer provider usage and label deterministic
estimates. Automatic summaries reuse outcomes; richer summaries require an explicit call.

## Consequences

Sessions remain resumable and auditable without background calls, but some accounting is estimated.
