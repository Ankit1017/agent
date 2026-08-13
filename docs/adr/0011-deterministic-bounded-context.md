# ADR 0011: Deterministic Bounded Context

## Status

Accepted.

## Context

Sending complete growing transcripts wastes provider context and repeats large tool results.
Additional model summarization calls would add latency and nondeterminism.

## Decision

Build the provider view with a pure `ContextBuilder` using serialized character counts. Preserve the
current request and valid tool protocol, compact older current results, include completed exchanges
newest-first, and evict the oldest until the configured budget fits. Never rewrite session history.

## Consequences

Input size is predictable without tokenizer or model dependencies. Character counts approximate
tokens, and an oversized essential prompt fails clearly instead of being silently truncated.
