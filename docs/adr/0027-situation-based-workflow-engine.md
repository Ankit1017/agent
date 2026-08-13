# ADR 0027: Deterministic Situation-Based Workflow Engine

## Status

Accepted.

## Decision

Place a deterministic, provider-neutral workflow coordinator above request tool routing. Ship 20
typed built-in workflows plus a general fallback. Select without an LLM call, constrain schemas by
the current stage, project stages into task plans, and gate completion using recorded evidence.
Required stages use hybrid enforcement while optional stages may be skipped. Explicit one-shot user
overrides take precedence over automatic selection.

Persist workflow runs in session schema version 7. Workflow progress contains observable summaries
only. Existing approval and guardrail policies remain separate and cannot be overridden by a
workflow.

## Consequences

Tool use becomes predictable and token-efficient, and interfaces can show the same stage state.
Built-in definitions and selection fixtures must be maintained together. User-defined workflows are
deferred until the contract is stable.
