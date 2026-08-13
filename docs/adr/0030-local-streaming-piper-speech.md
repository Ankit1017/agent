# ADR 0030: Local Streaming Piper Speech

## Status

Accepted.

## Context

The browser needs low-latency audible output that is independent of chat state and reusable by
future internal interfaces. Runtime downloads, server-side audio history, and provider-specific
types in the application would weaken reproducibility, privacy, and the clean architecture boundary.

## Decision

Add a provider-neutral `SpeechSynthesizer` port and bounded `SpeechService`, composed with the
maintained in-process Piper Python adapter only in `bootstrap.py`. Accept Piper's GPL-3.0 dependency.
Expose a localhost-only, cookie/Origin/CSRF-protected endpoint that streams s16le mono PCM with fixed
metadata headers. No submitted text or audio is persisted. Text is irreversibly redacted before it
reaches Piper. One global synthesis is allowed and cancellation closes the provider iterator.

Extend the same independent browser page with a separate model-only conversation boundary. Its
application service calls the configured `ModelClient` exactly once per successful turn with a fixed
voice-assistant prompt, bounded redacted conversation text, and no tools. It cannot use the agent,
workspace context, memory, workflows, or evaluations. Multiple schema-v1 conversations persist as
atomic redacted JSON in protected control-workspace state. Model aliases are the exact existing
`HARNESS_MODELS` allowlist. Full bounded text is retained, provider history uses the newest
`HARNESS_CONTEXT_MAX_CHARS`, and audio is never persisted.

Install fixed voices only through `scripts/setup-voices.ps1`; startup never downloads. English
Lessac is preloaded, while Hindi voices load lazily and remain cached. Priyamvada's source dataset is
CC BY-NC-SA 4.0 and Rohan has separate IITM terms, so both Hindi voices are restricted to local
noncommercial prototypes. Commercial use or redistribution requires replacement/relicensing and
legal review.

## Consequences

Warm English audio can begin before full synthesis completes, and future internal sources can reuse
the application service. CPU use is deliberately serialized. The React client must schedule PCM,
construct WAV downloads, and own replay memory. Exact phoneme lip-sync, public authentication,
server-side audio history and chat/CLI read-aloud remain deferred. Local browser speech recognition
is governed separately by ADR 0031.

The `/speech` UI therefore has a default Voice Conversation mode and retains Direct Text-to-Speech.
Markdown is displayed through the existing safe renderer and deterministically converted to plain
speech. Historical answers may be synthesized again without another model call. Deletion is an
explicit permanent text-history action; failed model calls never create partial turns.
