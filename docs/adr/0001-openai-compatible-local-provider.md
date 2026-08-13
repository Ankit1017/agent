# ADR 0001: OpenAI-Compatible Local Provider

## Status

Accepted.

## Context

The existing local stack exposes `gpt-oss:20b` through LiteLLM at an OpenAI-compatible endpoint.

## Decision

Use the OpenAI Python SDK behind the provider-neutral `ModelClient` port and the Chat Completions tool
protocol. Keep the model name, URL, and key in environment configuration.

## Consequences

The first adapter is small and matches the deployed gateway. Provider-specific objects cannot cross
into application/domain code, so another protocol requires a new adapter rather than an agent rewrite.

