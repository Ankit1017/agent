# High-Level Design

## Unified browser workspace

The React presentation uses one shared application shell across Chat, Speech, Voice Agents, and
optional modules. A route-aware module switcher, semantic design tokens, persistent theme/density
preferences, and reusable status/dialog/empty-state components keep navigation and feedback
consistent without sharing conversation or task state. Page layouts remain specialized: Chat keeps
its collapsible inspection panels, Speech prioritizes its transcript, and profile configuration uses
a searchable master-detail editor. The Studio branch consumes the same shell while remaining absent
from the protected main runtime.

## Local speaking avatar

Speech is an optional localhost-only subsystem, independent of chat sessions and workspace tasks.
The server accepts 1–5,000 sanitized characters, one exact allowlisted voice, and rates from 0.75
to 1.50. One synthesis may run globally; another request receives `429` instead of queueing. The
React `/speech` page schedules streamed signed 16-bit mono PCM after a small buffer, drives its SVG
mouth from Web Audio amplitude, and keeps only the current bounded utterance in browser memory for
replay or a client-created WAV download. English is preloaded; allowlisted Hindi voices load on
first use and remain cached. Stop and navigation abort playback and close the provider iterator.

The default `/speech` mode is a protected voice conversation. It maintains multiple named,
model-selectable text transcripts and makes exactly one tool-free model call per successful turn.
The complete bounded transcript remains visible, while only the newest 30,000 configured context
characters are sent. Markdown answers are capped at 1,500 characters, displayed safely, converted
to plain speech, and automatically synthesized. Any historical assistant answer can be spoken again
without another model call. Direct text-to-speech remains available as a second mode.

When local speech input is installed and enabled, Voice Conversation adds a per-page microphone
opt-in. Wake mode waits for “Hey Buddy”; tap mode listens immediately. Both end on silence or the
15-second bound, transcribe English/Hindi locally, and auto-submit only a non-empty sanitized final
transcript. Capture pauses through transcription, model generation, and assistant playback so the
assistant cannot trigger itself.

## Evaluation and controlled evolution

Evaluated requests receive a pre-call contract and a post-request deterministic observation and
handoff. Aggregate metrics cover verified completion, workflow accuracy, calls, tokens, context,
latency, failures, approvals, and guardrail events. Offline workflow fixtures provide a stable
baseline. Paired comparisons reject safety or verification regressions before considering quality
or efficiency gains. Candidate proposals are bounded review artifacts and never self-apply.

## Situation workflow subsystem

The application layer contains a fixed catalog of 20 common task playbooks, a deterministic
zero-call selector, and a request-scoped coordinator. The selected workflow narrows the tool router
to the current stage, projects stage state into the persisted plan, and evaluates recorded evidence
before completion. Explicit one-shot overrides are shared by CLI, Textual, and browser interfaces.
Low-confidence requests retain general adaptive routing. Workflow orchestration never replaces
approval, path, command, patch, web, secret, or plugin guardrails.

## Terminal presentation

The default interactive experience is a full-screen Textual application. It separates Markdown
conversation content from compact model/tool activity, provides a three-line composer, and uses
modal approval, session, event, and help screens. A capability detector chooses the original plain
interface for redirected output, `TERM=dumb`, or `NO_COLOR`.

The synchronous application service runs in one exclusive worker. A bridge implements the existing
approval and progress ports, sends UI mutations to the main thread, and blocks only the worker for
approval. Concurrent prompts are rejected while busy. The provider schema remains unchanged.

Each new prompt is followed by a collapsed `Working…` component. It consumes the same redacted
progress stream as the session sidebar, merges model lifecycle pairs, and ends as Completed,
Completed with issues, or Stopped with error. Schema-v4 request numbers reconstruct these components
beside their original prompts after resume; provider and tool protocols remain unchanged.

## Goals

Provide a comprehensible local assistant that can gather project context and help with terminal work
without autonomous mutation. Favor explicit control, replaceable external integrations, deterministic
tests, and a structure that can grow without importing framework concerns into business rules.

## Major subsystems

1. Configuration validates the local gateway, key, model, and execution limits. Per-request LLM
   calls default to 20 and resolve through CLI, saved-session, environment, then default precedence.
2. The CLI manages sessions and obtains human approval.
3. The agent loop coordinates provider-neutral messages and registered tools.
4. Inspection tools automatically read bounded, non-sensitive workspace content.
5. The PowerShell tool applies denial policy, approval, timeout, redaction, and output limits.
6. JSON persistence atomically stores versioned, redacted transcripts.
7. Progress reporting renders and persists compact model/tool lifecycle events without exposing
   reasoning or adding model calls.
8. Five v2 coding tools return versioned, bounded JSON envelopes for high-information inspection,
   navigation, editing, and verification.
9. `ContextBuilder` creates a deterministic 60,000-character provider view without changing the
   complete redacted transcript.
10. Local SearXNG discovers current sources; a guarded fetcher extracts selected public pages so
    search snippets are not treated as sufficient evidence.
11. Deferred routing selects a small tool profile, activates omitted capabilities explicitly, and
    stops repeated identical failures.
12. Git inspection, code intelligence, and persisted plans support Plan → Execute → Verify work;
    final verification is generated from recorded results.

## Failure handling

Configuration, model, tool, policy, and session failures use domain-specific errors. Tool failures are
returned to the model when it can recover; startup and persistence failures are shown without a
traceback. Nonzero commands and timeouts become structured tool results. Corrupt session files are
rejected and skipped by session listing.

## Non-functional requirements

- Synchronous and deterministic for v1.
- Network use is limited to the configured model, local SearXNG, SearXNG upstream engines, and
  guarded public page retrieval.
- Explicit bounds on turns, output, search files, matches, listing entries, and command duration.
- Fresh human approval for every patch and every detected project-check execution.
- Automatic read-only web access with visible progress, strict SSRF boundaries, and no browser or
  authentication capability.
- Full offline unit suite, strict typing, linting, and at least 85% coverage.
- Security-relevant behavior is implemented and tested outside prompts.
- Interactive wait lines are replaced on completion; redirected output remains append-only.

## Session productivity and extensibility

Schema-v6 sessions retain bounded summaries, event bookmarks, advisory token accounting, quota,
observable task plans, and completion evidence. Versions 1–5 remain readable. Prompt sanitization
precedes UI, storage, and model boundaries. Export, reversible
archive, integrity scan, and quarantine use dedicated application ports and protected local storage.
Installed tool entry points are discovered inertly and loaded only from an explicit trusted
allowlist; their results are redacted and bounded, but plugin Python execution is not sandboxed.

The browser GUI replaces Open WebUI in normal startup at `127.0.0.1:3000`. It runs on Windows so
approved host tools retain their behavior, while LiteLLM and SearXNG remain in Docker. The catalog
supports confirmed local workspaces; one task runs per workspace and two globally. Open WebUI is
preserved only through the manual `legacy-ui` profile on port 3001.

## Reusable project understanding

The first coding retrieval lazily indexes safe code, Markdown, and manifests. Later requests compare
metadata and refresh only changed files. Hybrid ranking combines local embedding similarity,
lexical overlap, exact names/paths, and recent-change boost. Six files and 12,000 characters are the
default retrieval ceiling. Project memory reduces repeated inspection; live files and checks remain
the authority for edits and completion claims.
`/speech/agents` manages reusable profiles for tool-enabled voice conversations. An agent profile
selects one registered workspace, exact model and tools, bounded execution/context settings, and
voice output. A conversation stores a full immutable snapshot; edits affect new conversations until
an idle, same-workspace upgrade is explicitly applied. Agent progress and visual approvals are
displayed, while only the final answer is sent to Piper.
