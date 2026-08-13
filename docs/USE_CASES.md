# Use Cases

## Switch the session model

The user lists aliases, selects one while idle, and sees the effective model. Unknown aliases and
busy-workspace mutations are rejected. The next request uses the saved selection; reset restores
the configured default.

## Evaluate a request

Before the first provider call, capture records the selected workflow, evidence expectations, and
limits. After the final answer, the service derives a score and handoff from saved observable state.
The user may attach an explicit pass/fail mark without overwriting the deterministic score.

## Compare and propose

An operator runs the offline suite, compares two compatible run IDs, and reviews the paired verdict.
They may explicitly request one model-generated proposal for an allowlisted component. Approve or
reject records the review decision only; implementation and verification remain separate guarded
work.

## Situation-based workflow execution

The sanitized request is scored against the built-in workflow catalog. An explicit or pending
override wins; low confidence uses general assistance. The selected workflow creates an observable
plan and exposes only current-stage schemas. Successful results advance the stage, optional stages
may be skipped, and two failures block a required stage. Final answers disclose unmet requirements.
See `WORKFLOWS.md` for all 20 playbooks.

## Use the full-screen terminal interface

Precondition: stdin and stdout are interactive, `TERM` is supported, and `NO_COLOR` is absent. The
harness shows recent conversation and activity, the user writes a multiline prompt and presses
Ctrl+Enter, and the agent runs in a worker. Progress updates the sidebar and the final Markdown answer
is added to conversation. On a narrow terminal, the user opens activity with Ctrl+E.

Immediately after submission, a collapsed `Working…` bar appears below the prompt. Its title follows
the latest observable action. The user may expand it without interrupting work. On completion it
shows the step count and duration; recoverable tool errors produce Completed with issues, while a
fatal request error produces Stopped with error. The answer appears after this component.

Alternate flow: automatic detection chooses the plain interface, or the user explicitly selects it
with `--ui plain`. The same slash commands and application use cases remain available.

## Approve in the TUI

The agent requests PowerShell execution. A modal shows the redacted reason, workspace, exact command,
and non-sandbox warning. Reject is focused. `Y` or Approve authorizes the command; `N`, Escape,
Reject, or application shutdown denies it. Optional rejection feedback returns to the tool result.

## Resume and inspect activity

Ctrl+R or `/sessions` opens a searchable saved-session table. Selecting a row switches the bound
agent, reloads at most 100 visible messages, and shows that session's complete progress history.
Ctrl+E or `/events` opens every persisted event; `/events <count>` limits the view without mixing raw
tool output into the conversation pane.

For schema-v5 history, each request's collapsed timeline is rebuilt before its matching answer.
Earlier schema versions keep their ungrouped activity in the sidebar because exact ownership was not
previously persisted.

## UC-1: Converse

The user starts the harness with valid configuration and enters a question. The agent sends the
conversation to the model and displays its final answer. Empty input is ignored. Gateway failure is
reported while the saved user message remains resumable.

Alternate flow: the first substantive answer contains raw HTML, an unbalanced fence, or unverifiable
web citations. If a call slot remains, the application requests one formatting correction while
preserving the original. A valid correction becomes the sole saved answer. If it fails, the original
normalized answer is displayed with a visible quality warning.

## UC-2: Inspect project context

The model requests a listing, file range, or literal search. The harness validates containment and
automatically returns bounded content. Protected, binary, invalid, or external paths return a tool
error, allowing the model to choose another approach.

## UC-3: Execute an approved command

The model proposes one PowerShell command and explanation. Command policy permits it, the CLI shows
the exact proposal and warning, and the user enters `y`. The executor runs it non-interactively and
returns status, exit code, output, timeout, and truncation metadata.

## UC-4: Reject and revise

The user presses Enter or enters anything other than `y`, optionally supplies feedback, and no
process starts. The rejection and feedback return to the model, which may explain an alternative or
propose a revised command.

## UC-5: Block a dangerous command

The denial policy matches a catastrophic or evasive pattern before approval. The command never
reaches the human approval adapter or executor. The model receives the policy reason.

## UC-6: Handle timeout or failure

An approved command exceeds its deadline or exits nonzero. The executor returns a structured error
with bounded partial output. The model may diagnose it within the remaining turn limit.

## UC-7: Resume

The user lists sessions or supplies a valid session ID. The repository validates schema and loads the
history. A workspace mismatch is visibly warned; all new tools still use the current launch workspace.

The CLI replays the five latest progress events. The user may run `/events` to show the latest 20 or
`/events <positive-count>` to select another amount.

## UC-8: Observe model and tool progress

Before each model request, the CLI shows its session-wide call number and waiting state. The model
returns a short observable-action summary in the same call. The harness replaces the wait line with
the summary, target, outcome, and duration, then emits one compact result line per requested tool.
Events are redacted and saved. Missing summary metadata produces a deterministic fallback rather than
hidden reasoning or a second model call.

## UC-9: Configure calls per request

The user may set `HARNESS_MAX_TURNS` before startup, pass `--max-turns N`, or run `/max-turns N` in
the REPL. Values outside 1–100 are rejected. `/max-turns` reports value and source. Runtime and CLI
overrides persist with the session; an explicit CLI value replaces a saved value. Reset clears the
session override and restores the startup environment/default baseline.

## UC-10: Inspect and navigate efficiently

The model calls `inspect_project`, then targets `find_code` and `read_files`. Results are versioned,
redacted, paginated or truncated, and protected paths remain inaccessible. Unsupported grammars use
bounded text search.

## UC-11: Apply an exact patch

The model supplies create, exact-replace, or hash-protected delete operations. The harness validates
the entire transaction and displays its redacted unified diff. Approval commits atomic replacements;
rejection, stale content, or validation failure leaves files unchanged.

## UC-12: Run a detected check

The model selects a non-fixing profile returned by `inspect_project`. The harness re-detects it,
applies command policy, displays the exact command, requires fresh approval, and returns bounded
status and output.

## UC-13: Research current public information

The model sends a user-derived query to `web_search`. Local SearXNG queries Brave, DuckDuckGo, and
Bing and returns ranked citation candidates. The automatic operation is visible in progress. If
SearXNG is unavailable, the model receives a structured tool error.

## UC-14: Read supporting webpages

The model selects up to five search results or user-supplied public URLs. Each destination, DNS
answer, redirect, robots rule, response type, and byte limit is checked before local extraction.
Valid pages and independent errors return together. Answers cite exact URLs and treat fetched text
as untrusted evidence rather than instructions.

## UC-15: Review and summarize a session

Each completed request refreshes a bounded overview without another call. `/session-info` displays
it with usage and tags. `/summarize` performs one explicit visible call; failure preserves the old
summary.

## UC-16: Tag and filter activity

The user adds or removes normalized labels using stable event sequence numbers. Ctrl+F and
`/events` filter by kind, status, tag, or text without changing persisted history.

## UC-17: Export, archive, and restore

Exports contain full re-redacted internals. Archive confirmation defaults to rejection; approval
creates and verifies a ZIP and checksum manifest before removing live JSON. Restore validates fixed
entries, checksum, workspace, schema, and collision.

## UC-18: Check and quarantine sessions

Startup reports corrupt artifacts without blocking. `/session-check` supplies stable finding IDs;
confirmed quarantine moves only an unchanged file into recoverable protected storage.

## UC-19: Load trusted tool plugins

Startup discovers entry-point metadata without importing packages. Only configured names load.
Missing, conflicting, or invalid enabled plugins fail clearly; `/plugins` reports status.

## UC-20: Use and manage the browser harness

The local user opens port 3000, selects a confirmed workspace and session, submits a prompt, watches
live and persisted activity, and receives the final answer. Commands and session actions use the
same application services as terminal interfaces. Workspace registration validates and displays an
exact canonical local path before confirmation; removal deletes no project/session data.

## UC-21: Approve in the browser

The task-origin browser sees the exact redacted command/diff and warning with Reject focused. Only
that client may approve. Escape, timeout, disconnect, task completion, and shutdown reject.

## UC-22: Route and discover tools

The sanitized request selects a bounded profile. If a capability is absent, `discover_tools`
activates matching schemas for the next call. Inactive calls are rejected, and a third identical
failed call is stopped with alternatives.

## UC-23: Inspect code and Git

`git_inspect` returns bounded status, diff, history, or blame without mutation.
`code_intelligence` navigates Python and TypeScript/JavaScript syntax and reports language-server
availability without installing dependencies.

## UC-24: Plan and verify coding work

The model maintains concise observable steps, advances at most one active step, and cannot complete
a verification-required plan without successful recorded evidence. The final answer lists changed
files, actual checks, and unresolved limitations.

## UC-25: Retrieve project memory

For a coding request, the harness lazily refreshes the workspace index, ranks relevant sources, and
injects one bounded context block for reuse across the request. If embeddings fail, it records a
warning and uses lexical retrieval. `/memory <query>` returns the same ranked evidence without an
LLM call.

## UC-26: Maintain project memory

The user runs `/index` to view freshness and savings, `/index refresh` for incremental maintenance,
or `/index rebuild` after a configuration/cache problem. Browser maintenance runs off the event
loop and Textual maintenance runs in a worker. Cache corruption is quarantined and regenerated.
