# ADR 0028: Workspace Evaluation and Controlled Candidates

## Status

Accepted.

## Context

The harness already records workflows, tools, evidence, usage, and answer-quality outcomes, but it
could not turn those observations into repeatable measurements. Direct self-editing would make
failures difficult to attribute and would broaden the existing approval boundary.

## Decision

Store redacted evaluation contracts, observations, runs, comparisons, handoffs, and proposals in a
versioned workspace-local SQLite database separate from session JSON. Derive scores
deterministically from existing observable evidence. Ship offline workflow fixtures and keep live
evaluation explicit. Restrict proposals to an allowlisted component registry, require a structured
single-call proposal, and make approval a review-state change only. The evaluation layer never
edits source, mutates Git, promotes a candidate, or bypasses existing approvals.

## Consequences

Evaluation remains reproducible and safe to run offline. Session schema stays at version 7. SQLite
adds a separate regenerable evidence store and corruption-recovery path. Implementing a proposal
still requires normal review, patch approval, checks, and a frozen-baseline comparison.

