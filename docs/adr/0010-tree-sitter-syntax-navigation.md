# ADR 0010: Tree-sitter Syntax Navigation

## Status

Accepted.

## Context

Repeated literal searches consume model calls and do not distinguish definitions, imports, and
references. Full language servers would add background processes and compiler-specific complexity.

## Decision

Use `tree-sitter-language-pack>=1.14,<2` behind `CodeFinder` for the documented language set.
Classify syntax nodes generically, describe references as syntactic only, fall back to bounded text
search, cache parsed trees in memory, and place grammar artifacts under protected `.harness/cache`.

## Consequences

Navigation becomes compact and offline across many languages. It is not semantic resolution, and
grammar upgrades require compatibility tests.
