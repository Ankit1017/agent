# ADR 0019: Allowlisted in-process tool plugins

## Status

Accepted.

## Decision

Discover `local_harness.tools` metadata without importing packages. Import only configured names,
validate their tools, and wrap outputs with redaction, bounds, and safe exception translation.
Enabled plugin Python is explicitly trusted and unsandboxed.

## Consequences

Installation remains inert, while enabling a plugin grants it the user's process authority.
