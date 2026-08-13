# Harness Evaluation and Controlled Evolution

The evaluation layer measures request quality and efficiency without changing harness source code.
It is workspace-local, redacted, and independent from resumable session JSON.

## Capture flow

When session capture is enabled, the agent writes a falsifiable contract before the first model
call. The contract records the selected workflow, required evidence, configured limits, prompt and
component fingerprints, and the expected observable outcome. At request completion, deterministic
projection builds an observation from progress events, workflow state, completion evidence, token
usage, and the selected assistant answer. It also writes a bounded handoff containing completed
work, failures, changed files, checks, unmet requirements, and the recommended next action.

No prompt text, hidden reasoning, unrestricted tool output, or secret-bearing value is stored.
References to technical events use their stable sequence numbers.

## Storage

Version-1 SQLite storage lives at `.harness/evaluations/evaluations.sqlite3`. Records include
contracts, observations, handoffs, offline runs, comparisons, and candidate proposals. The database
is protected harness state and is not part of schema-v7 session persistence. A corrupt database is
moved to a timestamped `evaluations.corrupt-*.sqlite3` file and recreated.

## Offline suites and comparisons

The built-in `core` suite contains one deterministic selector fixture for each of the 20 workflows.
It makes no model or network calls. Live evaluation is opt-in; mutation cases are not batch-run and
must pass through ordinary per-operation approvals.

Comparisons pair matching case IDs. Fewer than the configured minimum (10 by default) is
`insufficient_evidence`. A guardrail regression or lost verification is always `worse`. Otherwise,
the comparison applies the documented pass-rate and 10% efficiency thresholds and reports
`better`, `mixed`, or `worse`. Nothing is promoted automatically.

## Candidate proposals

Candidates may target only the system prompt, workflow selector rules, tool profiles, or context
budgets. `/candidate propose` makes one explicit recorded model call and requires structured JSON
containing predicted changes, evidence IDs, risks, rollback instructions, and an evaluation suite.
Approving a candidate changes only its review status; it never edits files. Implementation remains
a separate review and approved-patch activity.

## Commands

```text
/eval status
/eval contract [request-number]
/eval mark <pass|fail> [note]
/eval run <suite> [--live]
/eval compare <baseline-id> <candidate-id>
/eval history [count]
/handoff
/candidate propose [component]
/candidate show <id>
/candidate approve <id>
/candidate reject <id> [feedback]
```

`harness-eval run core` provides the CI/script entry point. The browser and Textual interfaces use
the same application service and repository as the plain CLI.

