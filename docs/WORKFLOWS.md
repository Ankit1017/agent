# Situation-Based Workflows

The harness selects one versioned workflow from sanitized request text without an LLM call. A
workflow exposes only the current stage's tools, projects its stages into the visible task plan,
and records transitions as progress events. Required stages are enforced; optional stages may be
skipped when the model requests a later permitted tool. All approvals and guardrails remain
independent and authoritative.

| Workflow | Primary stages | Completion gate |
|---|---|---|
| `project_orientation` | memory, inspection, dependencies, changes | Inspected architecture |
| `locate_code` | memory, navigation, symbol read | Exact code evidence |
| `explain_code` | memory, read, relationships | Inspected implementation |
| `diagnose_bug` | changes, memory, navigation, optional reproduction | Evidence-backed cause |
| `fix_failing_test` | reproduce, navigate, read, patch, verify | Change and successful check |
| `implement_feature` | memory, navigate, dependencies, patch, verify, review | Change and successful check |
| `safe_refactor` | baseline, memory, navigate, patch, verify, review | Change and successful check |
| `create_or_update_tests` | navigate, read, patch, verify | Change and successful check |
| `review_changes` | diff, changed context, navigation, optional checks | Severity-ranked findings |
| `dependency_upgrade` | dependencies, search, sources, patch, verify | Change, check, and sources |
| `configuration_change` | memory, inspect, patch, verify | Change and successful check |
| `documentation_update` | memory, read, patch, optional check | Recorded documentation change |
| `api_integration` | dependencies, search, sources, navigate, patch, verify | Change, check, and sources |
| `build_failure` | reproduce, inspect, read, optional patch, verify | Successful build check |
| `performance_investigation` | memory, navigate, baseline, optional patch, measure | Two measurements |
| `security_review` | diff, memory, pattern search, dependencies, advisories | Evidence-backed findings |
| `web_research` | search, source reading | Successfully read citations |
| `technology_comparison` | constraints, search, sources | Primary-source comparison |
| `windows_troubleshooting` | inspect, approved diagnosis, approved verification | Verified terminal state |
| `release_readiness` | Git, changes, checks, dependencies, docs | Successful quality gate |

`general_assistance` is the low-confidence fallback and retains adaptive tool routing. Selection
precedence is an explicit request value, a pending one-shot override, automatic scoring, then the
fallback. `/workflow use <id>` stores the next-request override; `/workflow auto` clears it.

Stage failures receive at most `HARNESS_WORKFLOW_STAGE_MAX_ATTEMPTS` attempts. A required stage then
blocks the workflow; an optional stage is skipped. Final answers include unmet requirements instead
of unsupported completion claims. Workflows describe observable execution only and never request or
persist chain-of-thought.
