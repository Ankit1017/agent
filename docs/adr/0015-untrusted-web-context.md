# ADR 0015: Untrusted Web Context and Citations

## Status

Accepted.

## Context

Webpages can contain prompt injection and large irrelevant content. Search snippets alone are weak
evidence, while full pages can exhaust the model context.

## Decision

Mark all web envelopes as untrusted, require page reads for substantive claims when available, and
instruct the model to prefer primary sources and cite exact URLs. Bound page and batch content;
compact older results to summaries, citation metadata, and content head/tail without rewriting saved
sessions. Keep only a 15-minute in-memory cache and clear it on session switch.

## Consequences

Answers receive stronger evidence with predictable context cost and durable citations. Prompt
injection cannot be eliminated completely, so the model must never treat webpage instructions as
authority.
