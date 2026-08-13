# ADR 0021: Confirmed browser workspace catalog

## Status

Accepted.

## Decision

Atomically persist named canonical paths under ignored local-AI runtime state. Require validation
and explicit confirmation, reject unsafe roots, and make removal metadata-only. Compose an isolated
runtime per entry using shared control-root provider settings.

## Consequences

The GUI can switch projects without arbitrary directory browsing. Sessions and path policies stay
inside the selected workspace.
