# ADR 0017: Prompt sanitization boundary

## Status

Accepted.

## Decision

Apply the central redactor before prompt display, persistence, or model submission. Show and send
only the sanitized value when recognizable credentials are present.

## Consequences

Recognized credentials do not cross observable boundaries, but redaction remains best-effort.
