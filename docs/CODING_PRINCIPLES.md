# Coding Principles

Evaluation scoring must remain a deterministic application policy. Infrastructure may persist its
records, but it must not decide pass/fail thresholds. New candidate-editable components require a
stable ID, bounded configuration snapshot, source hash, offline fixture coverage, and documentation.
Never turn proposal approval into automatic source modification.

## Design

- Keep one reason to change per module and one purpose per tool.
- Depend on small protocols at external boundaries; inject implementations through constructors.
- Keep domain models provider-neutral and guardrail decisions deterministic.
- Prefer composition and explicit data flow over inheritance, registries with hidden discovery, or
  mutable global state.
- Do not abstract code until a real boundary, policy, or second implementation justifies it.

## Python style

- Target Python 3.12 and pass strict mypy and Ruff.
- Use descriptive names, explicit return types, frozen slotted dataclasses for value objects, and
  domain exceptions for expected failures.
- Use Google-style docstrings for modules and public APIs. Comments explain intent or constraints,
  not syntax.
- Avoid generic utility modules. Place helpers in focused modules named for their responsibility.

## Errors, logging, and secrets

Translate external failures at adapter boundaries. Preserve causes with `raise ... from`. Do not
catch `Exception` outside a boundary where a third-party library exposes no stable base error. Never
include unredacted credentials in exceptions, output, logs, fixtures, snapshots, or sessions.

## Testing

Use Arrange-Act-Assert and observable behavior. Application tests use fake ports; adapter tests may
use temporary files and local child processes. Tests must not depend on ordering, the internet, or a
running model unless marked `live`. Every guardrail receives allow, deny, boundary, and bypass tests.

## Evolution

New behavior starts with a use case. Update interfaces only when the data flow requires it, implement
the smallest cohesive adapter, register it in bootstrap, add tests, and update design documentation.
Consequential decisions require an ADR that records context, choice, and consequences.

Lifecycle visibility crosses the `ProgressSink` port. Keep progress events provider-neutral,
redacted, bounded, and separate from conversation messages. Never add an LLM call only to summarize
another call and never expose chain-of-thought as progress.

Execution limits use shared domain validation. New configuration paths must document deterministic
precedence, show the effective value to users, and preserve backward-compatible session loading.

High-information tools use the shared versioned envelope and output budget. Context compaction is
deterministic application logic: preserve provider tool-call pairing, never mutate saved history,
and never silently truncate the current prompt. File mutation validates the complete transaction
before one-time exact approval.

Network adapters are synchronous and replaceable. Keep local-provider access separate from public
page fetching, inject HTTP/DNS seams for offline tests, and treat remote fields as untrusted. Never
add a URL-capable tool without public-network validation, redirect checks, bounded streaming, MIME
controls, redaction, and complete branch tests for its policy.

Session analytics remain provider-neutral: record provider usage when present and label estimates
otherwise. Automatic summaries cannot add model calls. Prompt sanitization occurs before display,
storage, and provider boundaries.

Session history is recoverable user data: archives use atomic writes, checksums, and fixed entries;
quarantine uses fresh identities and confirmation. Plugin discovery is inert unless allowlisted;
enabled plugins are trusted code, while schemas and results still require validation and bounds.

FastAPI and React are presentation dependencies. Keep browser coordination typed, version public
payloads, run blocking work outside the event loop, and reuse application use cases instead of
duplicating CLI behavior. Workspace catalogs and approval ownership are security policy and require
focused tests plus an ADR when changed.

Project-memory parsing and ranking remain deterministic core behavior. Infrastructure adapters may
persist SQLite rows or call loopback Ollama, but tests use fake embeddings and prove lexical
fallback, protected-path exclusion, redaction, bounds, and cache invalidation.
