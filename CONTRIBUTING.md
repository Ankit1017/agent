# Contributing

Changes to prompts, workflow triggers, tool profiles, or context budgets should run the offline
evaluation suite and preserve the baseline run ID. A recommendation needs paired evidence; safety
or verification regressions are release blockers. Candidate proposals are implementation briefs,
not authorization to modify code.

Workflow changes must update the typed catalog, deterministic selector fixtures, stage-transition
tests, all shared interface projections, `docs/WORKFLOWS.md`, and an ADR when orchestration or
persistence semantics change. Required stages must use observable tool evidence, never prompt-only
claims.

Create a virtual environment and run `scripts/setup.ps1`. Keep commits focused around one use case.

## Change checklist

1. Identify the use case and layer that owns the behavior.
2. Add or adjust a port before coupling application logic to an external service.
3. Implement guardrails before exposing a new capability to the model.
4. Add Arrange-Act-Assert tests using fakes; real gateways are opt-in only.
5. Update the relevant HLD, LLD, use-case, or guardrail document.
6. Add an ADR for consequential or hard-to-reverse architecture decisions.
7. Run `scripts/check.ps1` and include no `.env` or `.harness` data.

## Adding a tool

A tool must have one purpose, a closed JSON schema, explicit argument validation, bounded output,
documented side effects, and tests for malformed input. Read-only tools require workspace containment.
Any tool capable of mutation requires a code-enforced approval path. Register tools only in the
composition root.

Add a compact descriptor and profile classification for every new tool. Tools outside the initial
profile must be activated through `discover_tools`. Aliases must be explicit; fuzzy repair is
prohibited.

High-information tools must use the common versioned envelope. Add language grammars through the
explicit extension map with syntax and fallback tests. Changes to context selection, patch rules,
detected checks, or approval display require matching guardrail and design updates.

Web providers remain behind application ports. Tests use fake providers, HTTP transports, and DNS
resolvers by default. URL-policy changes require complete branch coverage and explicit SSRF,
redirect, proxy, MIME, robots, and response-limit review.

Session-schema changes require backward migration, atomic-write, and corruption tests plus an ADR.
Schema version 6 stores observable task plans and deterministic completion evidence.
Archive and quarantine changes must preserve redaction, checksums, race detection, and recovery;
never silently delete history.

Tool plugins use `local_harness.tools`, remain disabled by default, and document trust and side
effects. Test discovery without import, allowlisting, schema validation, collisions, exception
translation, redaction, and output limits.

See `docs/CODING_PRINCIPLES.md` for detailed conventions.

Browser changes also require React API/component tests, a production Vite build, and the Playwright
smoke suite. Keep FastAPI under `interfaces/web`; core layers must not import browser frameworks.
Update versioned REST/WebSocket tests whenever public payloads change.

Project-memory changes require deterministic scanner/ranker tests, lexical-fallback coverage,
SQLite invalidation checks, and proof that protected content is neither embedded nor stored. Do
not add external vector databases or LLM-generated summaries to the index.

Project-memory changes require deterministic scanner/ranker tests, lexical-fallback coverage,
SQLite invalidation checks, and proof that protected content is neither embedded nor stored. Do
not add external vector databases or LLM-generated summaries to the index.
