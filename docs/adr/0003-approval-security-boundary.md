# ADR 0003: Approval Security Boundary

## Status

Accepted.

## Context

Native PowerShell can escape its working directory, and v1 does not include OS-level isolation.

## Decision

Require one explicit approval for every free-form command after a non-overridable catastrophic-command
policy. Default to rejection and never remember approval.

## Consequences

The user retains control and native Windows compatibility. Approval is not a sandbox; documentation
and prompts must communicate the residual risk without overstating workspace containment.

