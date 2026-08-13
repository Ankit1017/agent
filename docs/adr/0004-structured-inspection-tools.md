# ADR 0004: Structured Inspection Tools

## Status

Accepted.

## Context

Automatically classifying arbitrary PowerShell as read-only is unreliable.

## Decision

Auto-run only structured directory listing, UTF-8 file reading, and literal text search. Enforce path
containment and content limits in their shared inspector. Treat all free-form PowerShell as mutating.

## Consequences

Routine context collection is low-friction and enforceable. The model has less inspection flexibility
than a raw shell, and additional read operations must be implemented as guarded tools.

