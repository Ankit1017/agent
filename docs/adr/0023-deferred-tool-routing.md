# ADR 0023: Deferred request-scoped tool routing

## Status

Accepted.

## Context

Sending every registered schema consumes context and makes small local models choose tools less
reliably.

## Decision

Select a deterministic request profile, expose at most eight schemas, and keep `discover_tools`
visible so up to five matching capabilities can be activated for later calls. Reject inactive tools
and stop a third identical failed invocation. Discovery never changes security policy.

## Consequences

Provider context is smaller and tool selection is clearer. New tools need catalog metadata and
profile tests. MCP descriptors may use the same catalog later, but MCP stays disabled until curated
routing passes its acceptance gate.
