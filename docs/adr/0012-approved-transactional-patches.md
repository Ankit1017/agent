# ADR 0012: Approved Transactional Patches

## Status

Accepted.

## Context

Editing through arbitrary shell commands is difficult to review and can partially modify a project.

## Decision

Provide structured create, exact-replace, and hash-protected delete operations. Validate the whole
transaction, display one redacted unified diff through `PatchApprovalGateway`, default to rejection,
detect changes after validation, and use atomic replacement with best-effort rollback.

## Consequences

Edits are precise and auditable while remaining native filesystem operations rather than an OS
sandbox. Every transaction requires fresh approval and large or ambiguous changes are rejected.
