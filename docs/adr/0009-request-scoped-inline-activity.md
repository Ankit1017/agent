# ADR 0009: Request-Scoped Inline Activity

## Status

Accepted.

## Context

The technical activity sidebar explains the complete session but does not visually connect work to
the prompt and answer that caused it. Call numbers alone cannot reconstruct exact request ownership
after a session resumes.

## Decision

Assign every submitted request a positive session-local `request_number` and persist it on all
messages and progress events produced by that request. Upgrade JSON sessions to schema version 4;
versions 1–3 load with null request numbers. Provider serialization omits this local metadata.

Render a collapsed-by-default `RequestActivity` between each tagged user prompt and answer. It uses
only redacted observable progress, merges model lifecycle pairs, and keeps tool events distinct. The
complete sidebar remains available.

## Consequences

New and resumed schema-v4 sessions can reconstruct exact request timelines. Older sessions remain
readable but cannot receive exact historical inline grouping. Session documents gain one nullable
integer per message and event; provider, tool, approval, and command-policy contracts do not change.
