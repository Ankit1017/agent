# Guardrails

## Browser usability and accessibility

- Shared UI components never receive credentials, raw provider payloads, restored secrets, or
  unredacted tool arguments.
- Theme, density, and collapsed-panel preferences contain presentation state only.
- Every mutation continues through the existing same-origin cookie, Origin, and CSRF boundary;
  visual controls do not weaken approval or policy enforcement.
- Normal text and interactive states target WCAG 2.2 AA contrast, keyboard focus remains visible,
  dialogs and mobile navigation support Escape, and visible status text accompanies animation.
- Layouts must not overflow horizontally at 390, 768, 1024, or 1440 CSS pixels and must respect
  `prefers-reduced-motion`.

## Local speech

- Speech text passes through `SecretRedactor` before synthesis and is never restored, logged,
  persisted, or returned.
- Voice IDs are an exact server allowlist; browser paths and arbitrary model identifiers are never
  accepted.
- Voice installation is a separate checksum-verified script with explicit noncommercial-license
  acceptance. Startup and requests never download models.
- Speech mutations retain localhost trusted-host, SameSite cookie, exact Origin, CSRF, and body-size
  enforcement.
- Audio2Face receives only already-redacted Piper PCM. The browser cannot choose executable, model,
  dependency, input, output, or working-directory paths and cannot pass process flags.
- Animated speech is limited to 60 seconds and one active request. Native process time and output are
  bounded; errors never expose raw process output or local paths.
- Request WAV/animation intermediates live only in a protected temporary directory and are deleted
  at the request boundary. Startup and first use never download SDK dependencies or model files.
- Each Audio2Face request owns a unique temporary directory and packed-weight file; no request can
  read, replace, or clean another request's output. Rig names/counts, timestamps, finite unit-range
  weights, encoding, and exact binary length are validated before browser serialization.
- The 3D avatar endpoints serve only checksum-verified GLBs installed by rights-confirming setup
  scripts. Selection uses an exact bounded catalog ID; browser paths are never accepted. External
  GLB resources and scenes beyond the
  documented 50 MB/complexity bounds are rejected before installation.
- CSP permits `blob:` only for Three.js decoding of images already embedded in the protected GLB;
  it does not permit external model, texture, script, API, or credential-bearing requests.
- Presenter body poses operate only on discovered local skeleton bones. They never alter, replace,
  infer, or suppress Audio2Face mouth, jaw, brow, or eye weights during speech.
- One global synthesis slot prevents unbounded CPU/memory queues. Disconnect and Stop close the
  iterator, and audio exists only in bounded browser memory for the current utterance.
- Voice-conversation turns bypass the agent, tools, project memory, workflow engine, evaluations,
  and workspace context. Code supplies an empty tool list and rejects tool-call output.
- Input, titles, and model output are redacted before provider submission or schema-v1 persistence.
  Successful turns save text atomically; audio, provider payloads, and failed partial turns are not
  stored.
- Reads require an issued local browser session. Mutations retain exact Origin and CSRF checks;
  deletion additionally requires the exact conversation identifier.
- Only one voice-conversation operation may generate or mutate at a time. Provider context uses the
  newest configured 30,000 characters, replies are capped at 1,500 characters, API pages at 100
  messages, and stored conversations at 1,000 messages.

## Model aliases

Switching accepts only exact `HARNESS_MODELS` aliases. It does not fuzzy-match, discover, or enable
arbitrary gateway entries. Credentials stay in the Python process, and selection cannot alter tool
approvals, routing, or safety policies.

## Evaluation evidence

Evaluation records are redacted and bounded before SQLite persistence or interface display. They
contain prompt/component fingerprints and event sequence references, not raw prompts, hidden
reasoning, unrestricted output, or credentials. Offline evaluation cannot execute tools. Live
mutation evaluation is never unattended and retains every command and patch approval. Candidate
approval cannot edit source, mutate Git, promote configuration, or bypass any safety policy.

## Workflow boundary

Workflow definitions are orchestration guidance, not security policy. They cannot authorize a
command, patch, unsafe path, private-network fetch, plugin, or protected-file access. Required stages
are advanced only from recorded tool outcomes. Progress and persistence contain observable stage
summaries, never hidden reasoning.

## Presentation controls

- Both terminal interfaces use the same code-enforced command and path policies.
- TUI progress and approval text pass through `SecretRedactor` before rendering.
- PowerShell approval focuses Reject, rejects on Escape, and rejects pending requests during exit.
- Assistant Markdown is display-only and is never executed or used to open links automatically.
- Final answers are normalized and checked before persistence. Raw model HTML, malformed fenced
  blocks, and web citations outside the successful-tool URL allowlist produce a corrective attempt
  or a visible warning; they never silently disappear.
- Browser answer links are limited to HTTP(S), require an explicit click, and open with isolation
  attributes. Redirected CLI output contains no ANSI or full-screen control sequences.
- Inline activity renders only redacted event summaries, tool names, statuses, and durations. It
  never renders raw arguments, command output, model scratchpads, or chain-of-thought.
- The composer is disabled during execution, preventing concurrent requests and overlapping
  approvals.
- Plain fallback does not emit full-screen terminal control sequences.
- Recognizable credentials are replaced before prompt display, persistence, or provider submission.
- Archive and quarantine confirmation defaults to rejection and rejects on Escape or shutdown.

The TUI improves representation, not isolation. Approved native PowerShell still runs with the
user's OS permissions and is not sandboxed.

## Enforced controls

- Inspection paths are canonicalized and must remain under the launch workspace.
- Protected path components include `.env`, `.git`, `.harness`, and common cloud/key stores.
- Inspection rejects binary/non-UTF-8 content and applies entry, file, match, line, and output limits.
- Every PowerShell call passes a deterministic deny policy and explicit human approval.
- Patches reject absolute, escaping, protected, or symlink paths; binary or invalid UTF-8 content;
  ambiguous replacements; stale delete hashes; conflicts; and post-validation races.
- Every patch displays its exact redacted unified diff and requires fresh approval. Commit failures
  trigger best-effort rollback from the validated in-memory originals.
- Project checks are limited to freshly detected non-fixing profiles and still pass command policy,
  approval, timeout, output bounding, and redaction.
- SearXNG is reachable only through its configured loopback adapter. External fetches allow HTTP(S)
  ports 80/443 without credentials and reject every empty, malformed, private, loopback, link-local,
  reserved, multicast, unspecified, metadata-service, or mixed DNS answer.
- Every redirect is resolved and revalidated; environment proxies, cookies, authentication, and
  automatic redirects are disabled. Robots exclusions, attachments, unsupported MIME types,
  downloads above 2 MB, and more than three redirects are rejected.
- Transient HTTP failures receive one bounded retry. The model is instructed to use direct returned
  source URLs, never content proxies, and to report persistent evidence gaps rather than loop.
- Web content and snippets are marked untrusted, redacted, bounded, and never rendered as progress
  details. The prompt prohibits following source instructions or sending workspace data in queries.
- Empty, disk-management, shutdown, encoded, dynamically evaluated, execution-policy bypass, and
  recognized root-deletion commands are blocked without override.
- Processes are non-interactive, time-bounded, output-bounded, and run without a PowerShell profile.
- Exact configured secrets, bearer headers, common secret assignments, and key-like values are
  redacted before display or persistence.
- Agent LLM calls are bounded to 1–100 per user request to prevent unattended loops; the default is
  20 and tool executions do not consume separate call slots.
- Only request-active tools execute. Deferred discovery changes schema visibility, never permissions;
  the third identical failing call is blocked to prevent retry loops.
- Git uses read-only direct argument arrays. Plan text is observable status, never hidden reasoning,
  and verification claims are generated from recorded tool outcomes.
- Progress summaries are normalized to one line, limited to 12 words and 120 characters, redacted,
  and never passed to tool implementations.
- Session summaries and exports are re-redacted and bounded; CSV content is formula-safe.
- Archives use fixed entries and checksum manifests; quarantine rejects stale file identities.
- Token budgets are advisory and provider/estimated usage is labelled.
- Plugin discovery never imports packages. Only explicitly allowlisted plugins execute, and their
  schemas, names, collisions, result types, output bounds, and errors are checked.

## Prompt guidance

The system prompt tells the model to inspect first, propose focused commands, explain intent, respect
the workspace, and never claim unconfirmed execution. This improves behavior but is not considered an
enforcement mechanism. It also requests observable `step_summary` text and explicitly prohibits
private reasoning or chain-of-thought; code bounds the result and supplies safe fallbacks.
It also requests concise GitHub Markdown, language-labelled fences, no raw HTML, and exact URLs from
successful web tools. These presentation instructions are backed by deterministic application
checks rather than trusted on their own.

## Approval semantics

Only an exact `y` approves the displayed command or patch. Enter defaults to rejection. Approval
applies once to that exact tool call and is not remembered. Rejection feedback returns to the model.

## Residual risk

PowerShell is not OS-sandboxed. An approved command can access other paths, the network, child
processes, or user resources. Pattern denial is necessarily incomplete, secret redaction is
best-effort, and model-provided explanations may be wrong. Users must inspect commands and should run
the harness under a restricted account or container when stronger isolation is required.

SearXNG is not anonymity: upstream engines receive queries, websites receive page requests, DNS
rebinding remains a residual race between validation and connection, and malicious content can
attempt prompt injection against the model.

Allowlisted plugins are trusted in-process Python and can bypass harness policy through OS APIs.
Enable only reviewed packages. Full exports may retain sensitive project context even after
recognized credentials are removed.

The browser binds only to `127.0.0.1`, validates Host and exact Origin, requires a SameSite session
and CSRF token for mutations, and applies a restrictive CSP. It never receives provider secrets.
Microphone streaming is disabled by default and requires explicit per-page browser permission. Its
WebSocket requires the issued cookie, exact Origin, and CSRF authentication before accepting
fixed-format bounded PCM. One global session is allowed; audio, partial hypotheses, and unsanitized
text are never logged or persisted. Frame/rate/time bounds, disconnect cleanup, and microphone
pause during assistant playback are enforced in code.
Workspace registration rejects drive roots, UNC/network paths, protected system directories,
symlink roots, and junctions. Browser approvals are owner-bound and default-reject on expiry,
disconnect, or shutdown. Browser presentation adds no OS sandbox to approved PowerShell.

Project indexing uses the existing workspace path policy and excludes `.env`, `.git`, `.harness`,
credential locations, symlink escapes, generated/vendor directories, binary/non-UTF-8 files, and
oversized files. Text is redacted before embedding and persistence. Embeddings remain local through
the fixed loopback Ollama endpoint. Indexed excerpts are untrusted hints: the model must verify live
content before edits or success claims. The cache is regenerable and never stores whole source
files.

Project indexing uses the existing workspace path policy and excludes `.env`, `.git`, `.harness`,
credential locations, symlink escapes, generated/vendor directories, binary/non-UTF-8 files, and
oversized files. Text is redacted before embedding and persistence. Embeddings remain local through
the fixed loopback Ollama endpoint. Indexed excerpts are untrusted hints: the model must verify live
content before edits or success claims. The cache is regenerable and never stores whole source
files.
- Voice-agent profiles bind one exact registered workspace and snapshot exact model/tool access.
  Filtering occurs before router and `discover_tools` construction; unavailable dependencies fail
  closed. Custom instructions cannot replace immutable safety rules. Workspace-changing upgrades
  are rejected, plugin tools require explicit selection, and command/patch/check approvals remain
  owner-bound visual clicks. Cancellation rejects pending approvals and prevents subsequent model
  or tool calls. Progress is visible but only the bounded final response may be spoken.
