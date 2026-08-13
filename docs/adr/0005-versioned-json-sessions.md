# ADR 0005: Versioned JSON Sessions

## Status

Accepted.

## Context

V1 needs local resume without a database or service dependency.

## Decision

Persist full provider-neutral transcripts as schema-versioned JSON beneath `.harness/sessions`, using
temporary-file replacement and redaction before serialization.

## Consequences

Sessions are transparent and portable within the workspace. Full transcripts can still contain
sensitive context that best-effort redaction misses, and future schema changes require migrations or
explicit rejection.

