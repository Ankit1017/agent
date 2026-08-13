# ADR 0026: Local SQLite Project Memory with Ollama Embeddings

## Status

Accepted.

## Context

Repeated project inspection consumed model calls and provider context. The harness needs reusable
workspace understanding without sending source to an external vector service or adding summary
LLM calls.

## Decision

Maintain a version-1 SQLite cache per workspace under protected `.harness/cache/project-memory`.
Deterministic parsing stores redacted metadata, symbols, dependencies, bounded excerpts, hashes,
Git deltas, and float32 vectors. `embeddinggemma` is called through Ollama's loopback batch embed
API. Retrieval combines semantic, lexical, exact-name/path, and changed-file signals. A lexical
fallback is mandatory. Retrieved context is ephemeral and shrinks before conversation history.

## Consequences

The first coding request may wait for indexing. Setup downloads an additional local model, while
normal startup remains offline. SQLite cache data is regenerable and separate from schema-v6
sessions. Indexed content is an untrusted navigation hint and must be checked against live files.
