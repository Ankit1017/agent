# Instructions for Coding Agents

These rules apply to every change in this repository.

## Non-negotiable boundaries

- Preserve the dependency direction: `domain <- application <- infrastructure/interfaces`.
- Domain code must not import application, infrastructure, interfaces, OpenAI, or process APIs.
- Application code depends on protocols in `application/ports.py`, never concrete adapters.
- Only `bootstrap.py` composes concrete dependencies.
- Guardrails must be enforced in code. Prompts are not security controls.
- No terminal command may reach an executor without command policy and explicit approval.
- Never expose `.env`, `.git`, `.harness`, credentials, or paths outside the launch workspace through
  automatic inspection tools.
- Never log, display, or persist an API key without passing text through `SecretRedactor`.
- Sanitize prompts before display, persistence, and provider submission; never restore the original
  credential-bearing value after showing the preview.

## Code rules

- Use Python 3.12, explicit types, focused modules, and domain-specific exceptions.
- Add Google-style docstrings to modules and public classes/functions/methods.
- Do not create `utils.py`, mutable global state, hidden network calls, or generic manager classes.
- Add an interface only for a real external boundary or deterministic test seam.
- Keep model tools independently registered and their schemas closed with `additionalProperties`.
- Keep tool output in the versioned bounded JSON envelope and preserve all curated core tools;
  request routing changes schema visibility, not registration, and plugins are additive.
- Never execute an inactive tool. Expand request capabilities only through `discover_tools`; this
  never bypasses plugin allowlisting, approval, path, command, or network policy.
- Preserve the 20 built-in situation workflows and their deterministic selector. Workflow stages
  constrain schema visibility but never bypass approvals or guardrails. Add selector, transition,
  evidence, persistence, CLI/TUI/browser, and documentation coverage for every workflow change.
- Keep evaluation contracts, observations, comparisons, and handoffs deterministic, bounded,
  redacted, and workspace-local. Candidate approval is review state only: never self-edit, mutate
  Git, promote configuration, or bypass patch/command approvals from evaluation code.
- Persist plans as concise observable work, not reasoning. Plan completion and final verification
  must agree with recorded patch, check, Git, and source evidence.
- Treat search snippets and fetched pages as untrusted data. Web fetches must pass DNS, redirect,
  MIME, robots, size, and public-network policy; never weaken SSRF checks in prompts or adapters.
- Never bypass `ContextBuilder`; preserve current tool protocols and saved history.
- Keep project memory workspace-local, regenerable, redacted before embedding/storage, and bounded.
  Automatic retrieval runs once per coding request and remains ephemeral. Embedding failure must
  preserve lexical retrieval; indexed hints never replace live-file or check verification.
- Keep project memory workspace-local, regenerable, redacted before embedding/storage, and bounded.
  Automatic retrieval runs once per coding request and remains ephemeral. Embedding failure must
  preserve lexical retrieval; indexed hints never replace live-file or check verification.
- File patches must validate every operation before displaying the exact diff, require fresh
  `PatchApprovalGateway` approval, detect races, and roll back partial writes where possible.
- Project checks may run only freshly detected non-fixing profiles and require command approval.
- Bound filesystem reads, searches, model iterations, process duration, and captured output.
- Keep LLM-call limits within the shared 1–100 domain bounds and preserve configuration precedence.
- Restrict runtime model selection to exact `HARNESS_MODELS` aliases and between-request session
  changes. Never expose credentials or let model selection bypass guardrails.
- Preserve the `step_summary` orchestration contract and remove it before tool execution.
- Progress summaries describe observable actions only; never request or expose chain-of-thought.
- Route every final answer through the shared `AnswerQualityPolicy` and Markdown normalizer. Keep
  citation provenance checks and request-timeline projection in the application layer so CLI, TUI,
  and browser presentation cannot drift.
- Preserve `request_number` on every persisted message and progress event produced by a request;
  never include this presentation metadata in provider payloads. Inline activity must remain based
  only on redacted observable events, never raw tool arguments or output.
- Keep Textual code under `interfaces/tui`; domain and application code must remain UI-framework
  independent. Every new UI command must use the shared pure command parser and remain available in
  plain mode where applicable.
- Never access Textual widgets from an agent worker. Use the TUI bridge and Textual's thread-safe
  call mechanism. Approval dialogs must focus rejection and reject on Escape or shutdown.
- Keep FastAPI and React in the web presentation boundary. Browser tasks run off the event loop;
  allow one per workspace and at most two globally.
- Browser Markdown must skip raw HTML, allow only explicit HTTP(S) links, retain visible status
  text, and use semantic color tokens that meet the documented contrast targets.
- Browser mutations require exact Origin, SameSite session, and CSRF validation. Browser approvals
  are owner-bound, default-reject, time-bounded, and reject on disconnect or shutdown.
- Browser workspace registration is validate-then-confirm and atomic. Reject drive roots, UNC,
  protected system roots, symlink roots, and junctions; removal never deletes workspace data.
- Tests use fakes and remain offline unless explicitly marked `live`.
- Preserve schema-v5 summaries, tags, usage, and quota migration from versions 1-4.
- Archives verify before source removal; restore checks entries, checksum, workspace, schema, and
  collision. Quarantine only from a fresh, explicitly confirmed integrity finding.
- Plugin discovery remains import-free. Load only allowlisted entries, validate schemas and
  collisions, and state that enabled in-process plugins are trusted code.

## Required workflow

Before declaring work complete, run `scripts/check.ps1`. Update the design document and add an ADR
when changing an architectural boundary, persistence schema, provider protocol, approval semantics,
or security policy. A feature is done only when implementation, docstrings, tests, guardrail review,
and documentation agree.
