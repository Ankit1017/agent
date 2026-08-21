# Local Terminal Harness

New sessions now use `OPENAI_MODEL=gpt-5.5`. The explicit
`HARNESS_MODELS=gpt-5.5,gpt-oss:20b` allowlist controls selectable LiteLLM aliases. Use `/models`,
`/model gpt-oss:20b`, or `/model reset` between requests. The browser exposes the same selector and
each session remembers its model.

The primary local experience is a browser GUI at <http://127.0.0.1:3000>. It is served by a
Windows-hosted FastAPI process so approved PowerShell and workspace tools retain host semantics.
LiteLLM and SearXNG remain local Docker dependencies; plain CLI and Textual TUI remain supported.

```powershell
local-ai\Start Local AI.cmd
local-ai\Open Local AI.cmd
```

For a location-independent launcher that starts every dependency and verifies its status, run
`./scripts/start-all.ps1`. Add `-Restart` to stop and freshly restart the complete stack. You can
also double-click **Start All Services.cmd** in the repository root. Startup is idempotent and does
not download models, container images, or dependencies.

The browser provides workspace/session navigation, Markdown conversation, request timelines, the
complete event audit, owner-bound approvals, summaries, tags, quotas, exports, archives, integrity
checks, and plugin status. It follows the system theme by default and offers persistent light and
dark modes. Answers use readable GitHub Markdown, safe syntax highlighting, labelled code blocks,
copy buttons, and HTTP(S)-only source links. Adding a workspace requires an exact path and explicit
confirmation.

A small, approval-based terminal agent for a local OpenAI-compatible model. It can inspect the
launch directory through bounded read-only tools. Every free-form PowerShell command is shown to
you and runs only after an explicit `y` approval.

For coding work, the agent can inspect a project, search syntax, batch-read files, propose exact
transactional patches, and run detected verification profiles. Every patch and project check is
shown and requires a fresh approval.

For current information, `web_search` queries locally hosted SearXNG and `read_web_pages` safely
extracts selected public pages. These read-only calls run automatically and appear in the activity
timeline. Search queries and page requests still leave the computer.

## Optional local speaking avatar

The independent <http://127.0.0.1:3000/speech> page streams local Piper speech without creating a
chat session or saving text/audio. Install the pinned English and Hindi voice files only after
reviewing their terms:

```powershell
scripts\setup-voices.ps1 -AcceptNonCommercialVoiceLicenses
```

Then set `HARNESS_TTS_ENABLED=true` in `.env` and restart the browser server. English Lessac is
preloaded; Hindi voices load on first use. Priyamvada is CC BY-NC-SA 4.0 and Rohan has separate IITM
dataset terms, so the Hindi pack is for local noncommercial prototypes only. Commercial use or
redistribution requires replacement/relicensing and legal review. Piper itself is GPL-3.0.

`/speech` opens in **Voice Conversation** mode. It stores redacted text conversations locally,
allows an exact configured model alias, and makes one LLM call per turn with conversation history
only—no harness tools, project context, workflows, or agent loop. The Markdown reply is displayed
and spoken automatically. Audio is never saved, and saved answers can be synthesized again without
another model call. The original **Direct Text-to-Speech** workflow remains available in its own
tab.

Use **Configure agents** or open <http://127.0.0.1:3000/speech/agents> to create reusable,
workspace-bound voice-agent profiles. Profiles snapshot an exact model, exact tool allowlist,
execution/context bounds, optional sanitized instructions, and voice preferences into each new
conversation. Protected Voice Chat remains the immutable one-call/no-tools default. Tool-enabled
turns use the normal browser progress stream and click-only approvals; only their final answer is
spoken. Stop requests cooperative cancellation and rejects pending approvals.

### Optional local wake word and speech input

Voice Conversation can capture local microphone input, detect **Hey Buddy**, transcribe English or
Hindi, auto-submit sanitized text through the same one-call model boundary, and re-arm after Piper
finishes. Raw microphone audio is never written to disk or sent off-machine.

```powershell
scripts\setup.ps1
scripts\setup-speech-input.ps1 -AcceptModelLicenses
```

Then set `HARNESS_STT_ENABLED=true` in `.env` and restart. Chrome/Chromium requires one explicit
**Enable Hey Buddy** click per page visit. Tap-to-talk is also available. Both modes stop after
silence or 15 seconds. Setup installs checksum-pinned artifacts under
`.harness/models/speech-input`; startup and first use never download models.

## Setup

Requirements: Windows, Python 3.12, the local LiteLLM gateway, and `gpt-oss:20b`.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Replace the placeholder `OPENAI_API_KEY` in `.env` with the value printed by
`local-ai\Show Gateway Credentials.cmd`, then start:

```powershell
harness
# or
python -m local_harness
```

Interactive terminals open a full-screen Textual interface with a Markdown conversation pane,
live activity sidebar, multiline composer, approval dialogs, and session/event pickers. Use
`Ctrl+Enter` to send, `Ctrl+R` for sessions, `Ctrl+E` for events, `Ctrl+H` for help, and `Ctrl+Q`
to exit. Terminals below 100 columns hide the sidebar; activity remains available with `Ctrl+E`.

Every prompt also gets an inline, collapsed `Working…` bar before its answer. Expand it with Enter,
Space, or the mouse to see a concise timeline of model actions, tools, statuses, and durations. It
shows observable progress only—not private reasoning, raw tool output, or command arguments. These
request timelines are saved and reconstructed when schema-v5 sessions resume.

Use `harness --ui plain` for the original line-oriented interface or `harness --ui tui` to force
the full-screen interface. The default `--ui auto` selects plain output when input/output is
redirected, `TERM=dumb`, or `NO_COLOR` is set.

The default limit is 20 LLM calls per user request. Configure it in `.env` with
`HARNESS_MAX_TURNS=20`, override startup with `harness --max-turns 30`, or use
`/max-turns 30` without restarting. `/max-turns` shows the effective value and source;
`/max-turns reset` returns to the startup/environment value. Valid limits are 1–100.

Resume with `harness --resume <session-id>`. Inside the REPL, `/help` lists conversation, event,
summary, quota, tagging, export, archive, integrity, plugin, and session commands. Ctrl+F opens the
searchable event view in the TUI.

Schema-v5 sessions maintain a bounded outcome summary, event bookmarks, and model token usage.
`/summarize [id]` explicitly spends one additional call for a richer overview. Token budgets are
advisory: `HARNESS_SESSION_TOKEN_BUDGET=0` disables them and
`HARNESS_TOKEN_WARNING_PERCENT=80` controls the first warning.

`/export md|csv [id]` writes a full redacted record under `.harness/exports`. `/archive <id>` creates
a checksum-bound archive, `/restore <id>` reverses it, and `/session-check` reports corrupt files.
Confirmed quarantine moves unchanged findings into recoverable `.harness/corrupt` storage.

Python entry points in `local_harness.tools` remain inert unless named in
`HARNESS_ENABLED_PLUGINS`. `/plugins` shows their status. Enabled plugins are trusted in-process
Python code and are not sandboxed.

## Intelligent tool routing

Each request starts with a compact `coding`, `web`, `system`, or `general` profile and sends at most
eight schemas. `discover_tools` activates missing capabilities without injecting the full catalog.
Coding tools include read-only `git_inspect`, syntax-aware `code_intelligence`, and persisted
`task_plan`. Use `/tools [query]` to inspect the catalog and `/plan` to view the latest plan.

Configure routing with `HARNESS_TOOL_PROFILE`, `HARNESS_TOOL_SCHEMA_LIMIT`, and
`HARNESS_TOOL_ACTIVATION_LIMIT`. Optional Python and TypeScript language servers are detected from
the `HARNESS_LSP_*_COMMAND` settings; nothing is installed automatically. Final answers gain a
deterministic Verification section after edits or checks.

## Situation-based workflows

Twenty deterministic playbooks now guide common coding, review, research, dependency, build,
security, performance, Windows, and release tasks. Selection consumes no model call and exposes only
the current stage's tools. Required stages and verification are enforced from recorded tool
evidence. Use `/workflows [query]`, `/workflow status`, `/workflow use <id>`, or `/workflow auto`.

Defaults are `HARNESS_WORKFLOW_MODE=auto`, `HARNESS_WORKFLOW_CONFIDENCE_MIN=0.60`, and
`HARNESS_WORKFLOW_STAGE_MAX_ATTEMPTS=2`. See [Workflows](docs/WORKFLOWS.md).

## Evaluation and controlled improvement

The harness records redacted request contracts, deterministic outcome metrics, and concise handoff
snapshots in `.harness/evaluations/evaluations.sqlite3`. `/eval status`, `/eval history`,
`/eval contract`, `/eval mark`, `/eval run core`, and `/eval compare` expose the evidence. The same
offline suite is available as `harness-eval run core`.

`/candidate propose [component]` spends one explicit model call to create a bounded improvement
brief for an allowlisted prompt, workflow, tool-profile, or context-budget component. Approve and
reject only record review state: proposals never edit source or Git. See
[Evaluation and Controlled Evolution](docs/EVALUATION.md).

Model input is bounded with `HARNESS_CONTEXT_MAX_CHARS=30000`. Batch reads default to eight files
(`HARNESS_BATCH_MAX_FILES=8`) and patch transactions to 100,000 characters
(`HARNESS_PATCH_MAX_CHARS=100000`). Full redacted sessions remain saved; only the provider view is
compacted.

## Project memory

Coding requests lazily maintain a reusable SQLite index at
`.harness/cache/project-memory/index.sqlite3`. It stores redacted metadata, dependency facts,
symbols, bounded excerpts, and local `embeddinggemma` vectors—not complete source files. Automatic
retrieval runs once per coding request and is reused by every model call without becoming a saved
chat message. If Ollama embeddings are unavailable, deterministic lexical retrieval continues.

Use `/index`, `/index refresh`, or `/index rebuild` to inspect and maintain the cache. Use
`/memory <query>` to inspect ranked sources without an LLM call. The coding profile also exposes
`project_memory`, `read_symbol`, `changed_context`, and `dependency_context`. Setup downloads
`embeddinggemma` once; normal startup never pulls it.

Web defaults are `SEARXNG_BASE_URL=http://127.0.0.1:8080`, eight search results, five pages,
12,000 characters per page, 30,000 characters per batch, and a 15-second network timeout. Run
`local-ai\Setup Local AI.cmd` once to provision the pinned SearXNG image.

Each model and tool call prints a compact progress line with its session-wide call number, action,
target, status, and duration. The model supplies observable action summaries in the same request;
hidden reasoning is never requested. Progress events are saved with the session. The TUI sidebar and
Ctrl+E event viewer show the complete active-session history; `/events <count>` limits the viewer to
the latest requested number. Plain mode continues to replay five events on resume and defaults
`/events` to the latest 20.

Assistant Markdown is rendered as display-only content. It is never executed and links are not
opened automatically. The visible TUI transcript is limited to the latest 100 conversational
messages; the complete bounded session remains persisted.

Before persistence, final answers pass through a shared deterministic quality check. It normalizes
safe Markdown, rejects raw HTML, detects unbalanced code fences, and validates web citations against
URLs returned by successful web tools. The agent may spend one configured call slot correcting an
invalid answer. If correction fails, the original substantive answer is retained with a visible
warning instead of being lost.

## Security model

Structured listing, reading, searching, and patching are code-limited to the launch workspace and exclude
credentials and harness metadata. PowerShell is **not sandboxed**. Its working directory is the
workspace, but an approved command can access the rest of the computer with the user's normal
permissions. Review commands carefully. Known catastrophic and policy-evasion commands are always
blocked, but a deny list cannot recognize every harmful command.

Full transcripts are saved under `.harness/sessions` after best-effort secret redaction. Recognized
credential text is previewed as `[REDACTED]`; only the sanitized prompt is displayed, saved, and
sent. Redaction remains best-effort, so avoid secrets in prompts and command output.

Fetched pages are untrusted input. The fetcher blocks private networks, unsafe redirects,
credentials, non-web schemes, attachments, unsupported content types, and oversized responses. It
does not provide login, JavaScript execution, downloads, PDFs, or browser automation. Transient
rate limits receive one bounded retry; persistent source failures are reported rather than hidden.

## Development

```powershell
scripts\setup.ps1
scripts\format.ps1
scripts\check.ps1
scripts\test.ps1
npm ci --prefix web
npm run dev --prefix web
npm run test --prefix web
npm run build --prefix web
```

`scripts\check.ps1` validates Python and browser code. Normal startup serves the existing
`web/dist` build without downloading dependencies.

The complete design is in [Architecture](docs/ARCHITECTURE.md),
[High-Level Design](docs/HIGH_LEVEL_DESIGN.md), [Low-Level Design](docs/LOW_LEVEL_DESIGN.md),
[Use Cases](docs/USE_CASES.md), and [Guardrails](docs/GUARDRAILS.md). Contributors must follow
[Coding Principles](docs/CODING_PRINCIPLES.md) and [CONTRIBUTING.md](CONTRIBUTING.md).
