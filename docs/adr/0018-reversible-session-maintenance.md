# ADR 0018: Reversible session maintenance

## Status

Accepted.

## Decision

Archive only after default-reject confirmation, verifying a fixed-entry ZIP and SHA-256 manifest
before source removal. Restore checks checksum, workspace, schema, and collision. Quarantine only a
fresh identity-bound integrity finding.

## Consequences

Maintenance remains recoverable and race-aware at the cost of protected metadata and confirmation.
