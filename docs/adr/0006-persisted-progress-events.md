# ADR 0006: Same-Call Persisted Progress Events

## Status

Accepted.

## Context

Local model calls can take tens of seconds, and the prior CLI remained silent while the agent gathered
context. Separate summary requests would double model latency and token use.

## Decision

Record provider-neutral model/tool lifecycle events in session schema v2 and publish them through a
`ProgressSink`. Require short observable summaries inside existing tool arguments and final response
markers. Strip orchestration metadata before tool execution and never request chain-of-thought.

## Consequences

Users see continuous compact progress and can review it after resume without extra model calls.
Model formatting is not guaranteed, so deterministic fallbacks remain necessary. Version-1 sessions
load with empty event history and upgrade on their next save.
