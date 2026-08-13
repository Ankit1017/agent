# ADR 0008: Full-Screen Terminal Presentation

## Status

Accepted.

## Context

Raw line-oriented input mixed conversation, progress, and approvals. The application ports already
separated progress and approval behavior, but the composition root constructed console adapters
directly. The harness needs a clearer interactive representation without coupling core use cases to
a UI framework or breaking redirected output.

## Decision

Use Textual 8.2.x under `interfaces/tui` for supported interactive terminals. Inject presentation
adapters through the composition root. Run the synchronous agent in one exclusive thread worker and
marshal UI changes through Textual's thread-safe call API. Keep console mode and select it
automatically for redirected streams, `TERM=dumb`, or `NO_COLOR`. Share pure slash-command parsing
between both interfaces.

No domain or session-schema changes are introduced. Approval remains the security boundary and its
modal defaults to rejection.

## Consequences

Interactive use gains Markdown rendering, separated activity, multiline input, and modal session
workflows. Textual becomes a runtime dependency and headless UI tests are required. In-flight model
calls still stop only at their synchronous call boundary. Plain mode remains stable for automation
and limited terminals.
