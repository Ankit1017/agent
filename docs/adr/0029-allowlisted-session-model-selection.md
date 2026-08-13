# ADR 0029: Allowlisted Session Model Selection

## Status

Accepted.

## Context

The local LiteLLM gateway exposes both `gpt-5.5` and `gpt-oss:20b`. A startup-only setting forced
users to restart and did not let saved sessions retain their selected model.

## Decision

Use `OPENAI_MODEL` as the default for new sessions and `HARNESS_MODELS` as the explicit comma-
separated alias allowlist. The existing session `model` field stores the selection, so schema 7 is
unchanged. Switching is allowed only between requests and creates an adapter for the exact alias.
CLI, Textual, and browser interfaces share the operation. Arbitrary gateway models are not enabled.

## Consequences

New sessions default to `gpt-5.5`; existing sessions retain their saved model until changed. New
LiteLLM aliases must be added to `HARNESS_MODELS`. Credentials remain server-side, and selection
does not change approvals or guardrails.
