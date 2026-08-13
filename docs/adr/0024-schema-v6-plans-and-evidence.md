# ADR 0024: Schema-v6 plans and completion evidence

## Status

Accepted.

## Decision

Persist `TaskPlan`, `TaskStep`, and `CompletionEvidence` in session schema version 6. Migrate versions
1–5 with empty collections. Build evidence deterministically and append verification when files were
changed or checks were executed.

## Consequences

Plans resume across interfaces, and completion claims have an authoritative record. Plans contain
concise observable status only and never hidden reasoning.
