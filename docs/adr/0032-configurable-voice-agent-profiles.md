# ADR 0032: Configurable Voice-Agent Profiles

## Status

Accepted.

## Context

Protected Voice Chat intentionally performs one tool-free model call. Some local voice workflows
also need bounded workspace inspection, research, or coding capabilities without weakening the
existing harness boundaries or letting a later profile edit silently change an active conversation.

## Decision

Store revisioned, sanitized voice-agent profiles as atomic schema-versioned JSON beneath the
control workspace's protected `.harness/voice-agent-profiles` directory. A profile binds exactly
one registered workspace, one configured model alias, an exact tool allowlist, context/workflow and
execution bounds, and Piper output preferences. New agent conversations copy the entire profile into
an immutable snapshot. An explicit idle-time upgrade may replace a snapshot only when the workspace
is unchanged. Deleting a profile does not delete its snapshots.

The composition root projects the snapshot into a provider-neutral execution policy. It filters the
tool registry before request routing is constructed, so `discover_tools`, workflow routing, and model
schemas cannot reveal or activate tools outside the snapshot. Custom instructions are appended below
the immutable harness system rules. Project-memory retrieval is omitted when disabled. Missing exact
workspace, model, or tool dependencies make the snapshot unavailable; access is never widened or
silently narrowed.

Agent turns use the existing workspace-exclusive, globally bounded browser task coordinator and its
owner-bound visual approval gateway. Command, patch, and project-check actions still require an exact
click approval. Cooperative cancellation rejects pending approvals and prevents later model/tool
boundaries; a synchronous operation already in progress is reported as cancellation-requested until
it returns. Only the bounded final answer is spoken. Progress, approvals, provider payloads, tool
arguments, microphone PCM, and generated audio are not persisted by this integration.

Protected Voice Chat remains immutable and compatible with schema-v1 conversations, retaining its
single-call, empty-tools behavior.

## Consequences

Profiles are reusable and auditable while conversations remain reproducible. Workspace changes
require a new conversation, and removed dependencies can make an old snapshot unavailable until the
exact dependency returns. In-process plugin tools remain trusted code, require global enablement and
explicit profile selection, and are never included by a built-in template automatically.
