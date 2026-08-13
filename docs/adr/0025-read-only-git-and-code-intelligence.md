# ADR 0025: Read-only Git and layered code intelligence

## Status

Accepted.

## Decision

Provide Git overview, diff, history, and blame through fixed argument arrays without a shell. Provide
Python and TypeScript/JavaScript navigation through Tree-sitter, report configured language-server
availability, and never install dependencies automatically. Approved project checks remain the
authoritative diagnostics mechanism.

## Consequences

Coding requests need fewer low-level searches while Git remains non-mutating. Compiler-grade
diagnostics depend on an already installed server or a detected approved check profile.
