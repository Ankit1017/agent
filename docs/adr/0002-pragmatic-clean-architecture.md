# ADR 0002: Pragmatic Clean Architecture

## Status

Accepted.

## Context

The harness will gain capabilities over time, but premature framework and interface growth would hide
the safety-critical flow.

## Decision

Separate domain, application, infrastructure, interfaces, guardrails, and composition. Introduce
ports only for actual external services or deterministic test seams. Enforce dependency direction
with architecture tests.

## Consequences

External technologies remain replaceable and tests stay offline. Contributors must maintain several
small modules, but no dependency-injection framework or abstract base hierarchy is required.

