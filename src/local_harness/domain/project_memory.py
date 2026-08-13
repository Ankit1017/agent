"""Provider-neutral project-memory entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ProjectIndexStatus:
    """Current state of one workspace project-memory index."""

    available: bool
    generation: int = 0
    files: int = 0
    symbols: int = 0
    dependencies: int = 0
    embedding_model: str = ""
    embedding_dimensions: int = 0
    configuration_fingerprint: str = ""
    embedding_available: bool = False
    retrieval_mode: Literal["semantic", "lexical", "unavailable"] = "unavailable"
    updated_at: str = ""
    stale: bool = True
    warning: str = ""


@dataclass(frozen=True, slots=True)
class IndexedFile:
    """Compact metadata retained for one safe workspace file."""

    path: str
    category: str
    language: str
    size: int
    mtime_ns: int
    sha256: str
    summary: str


@dataclass(frozen=True, slots=True)
class IndexedSymbol:
    """One source symbol with a stable content-bound identifier."""

    symbol_id: str
    path: str
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    signature: str
    sha256: str


@dataclass(frozen=True, slots=True)
class DependencyFact:
    """One dependency parsed from a project manifest."""

    ecosystem: str
    name: str
    constraint: str
    manifest: str
    line: int


@dataclass(frozen=True, slots=True)
class IndexDelta:
    """File changes between two successful index generations."""

    generation: int
    created: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    renamed: tuple[str, ...] = ()
    changed_symbols: tuple[str, ...] = ()
    git_head: str = ""
    git_status: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectMemoryQuery:
    """A bounded semantic and lexical project-memory query."""

    text: str
    category: Literal["architecture", "symbol", "documentation", "dependency", "all"] = "all"
    max_results: int = 8


@dataclass(frozen=True, slots=True)
class ProjectMemoryHit:
    """One ranked project-memory result."""

    source_id: str
    category: str
    path: str
    start_line: int
    end_line: int
    title: str
    summary: str
    sha256: str
    score: float
    changed: bool = False


@dataclass(frozen=True, slots=True)
class RetrievedProjectContext:
    """Bounded provider context assembled from project-memory hits."""

    summary: str
    hits: tuple[ProjectMemoryHit, ...]
    rendered: str
    retrieval_mode: Literal["semantic", "lexical"]
    generation: int
    candidate_chars: int
    injected_chars: int
    warning: str = ""
