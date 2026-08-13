"""Tree-sitter-backed syntactic code search with bounded text fallback."""

from __future__ import annotations

from pathlib import Path
from threading import current_thread, main_thread
from typing import Literal

from tree_sitter import Language, Node, Parser, Tree
from tree_sitter_language_pack import PackConfig, configure, get_language

from local_harness.domain.errors import ToolExecutionError
from local_harness.guardrails.path_policy import WorkspacePathPolicy

SymbolKind = Literal["any", "definition", "import", "reference"]

_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".html": "html",
    ".css": "css",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".java": "java",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".sh": "bash",
    ".ps1": "powershell",
}
_DEFINITION_TYPES = frozenset(
    {
        "function_definition",
        "function_declaration",
        "function_item",
        "function_statement",
        "method_definition",
        "method_declaration",
        "class_definition",
        "class_declaration",
        "class_specifier",
        "interface_declaration",
        "struct_item",
        "struct_specifier",
        "type_declaration",
    }
)


class CodeFinder:
    """Search supported source trees and cache parsed trees for one process."""

    def __init__(
        self,
        policy: WorkspacePathPolicy,
        *,
        cache_directory: Path,
        max_files: int = 2_000,
        max_file_bytes: int = 1_000_000,
    ) -> None:
        """Configure Tree-sitter extraction and deterministic resource limits."""
        self._policy = policy
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes
        self._cache: dict[tuple[str, int, int, str], Tree] = {}
        self._parsers: dict[str, Parser] = {}
        self._languages: dict[str, Language] = {}
        cache_directory.mkdir(parents=True, exist_ok=True)
        configure(PackConfig(cache_dir=str(cache_directory)))

    def clear_cache(self) -> None:
        """Discard parsed trees without touching the on-disk grammar cache."""
        self._cache.clear()
        self._parsers.clear()
        self._languages.clear()

    def find(
        self,
        query: str,
        requested_path: str,
        kind: SymbolKind,
        languages: list[str],
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, object]], bool, str | None]:
        """Return syntactic matches, truncation state, and the next numeric cursor."""
        if not query.strip():
            raise ToolExecutionError("query cannot be empty")
        if kind not in {"any", "definition", "import", "reference"}:
            raise ToolExecutionError("kind must be any, definition, import, or reference")
        if not 1 <= limit <= 100:
            raise ToolExecutionError("limit must be between 1 and 100")
        try:
            offset = int(cursor) if cursor is not None else 0
        except ValueError as exc:
            raise ToolExecutionError("cursor must be a non-negative integer") from exc
        if offset < 0:
            raise ToolExecutionError("cursor must be a non-negative integer")
        root = self._policy.resolve(requested_path)
        if not root.is_dir():
            raise ToolExecutionError(f"Not a directory: {requested_path}")
        allowed = {_normalize_language(language) for language in languages}
        matches: list[dict[str, object]] = []
        files_seen = 0
        for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
            if files_seen >= self._max_files or len(matches) >= offset + limit + 1:
                break
            language = _EXTENSIONS.get(path.suffix.casefold())
            if not path.is_file() or self._policy.is_protected(path):
                continue
            if allowed and (language is None or language.casefold() not in allowed):
                continue
            files_seen += 1
            if path.stat().st_size > self._max_file_bytes:
                continue
            try:
                source = path.read_bytes()
                source.decode("utf-8")
                if b"\x00" in source[:8192]:
                    continue
                if language is None:
                    matches.extend(self._text_matches(path, source, query, kind))
                    continue
                try:
                    tree = self._tree(path, source, language)
                    matches.extend(self._matches(path, source, tree, language, query, kind))
                except RuntimeError:
                    matches.extend(self._text_matches(path, source, query, kind))
            except (OSError, UnicodeDecodeError, RuntimeError):
                continue
        page = matches[offset : offset + limit]
        truncated = len(matches) > offset + limit or files_seen >= self._max_files
        next_cursor = str(offset + len(page)) if truncated and page else None
        return page, truncated, next_cursor

    def _tree(self, path: Path, source: bytes, language: str) -> Tree:
        if current_thread() is not main_thread():
            raise RuntimeError("Tree-sitter parsing is unavailable in Windows worker threads")
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns, language)
        tree = self._cache.get(key)
        if tree is None:
            parser = self._parsers.get(language)
            if parser is None:
                language_definition = get_language(language)
                parser = Parser(language_definition)
                self._languages[language] = language_definition
                self._parsers[language] = parser
            tree = parser.parse(source)
            self._cache[key] = tree
        return tree

    def _matches(
        self,
        path: Path,
        source: bytes,
        tree: Tree,
        language: str,
        query: str,
        kind: SymbolKind,
    ) -> list[dict[str, object]]:
        matches: list[dict[str, object]] = []
        folded = query.casefold()
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            category = _node_category(node)
            candidate = _candidate_text(node, source, category)
            requested = kind == "any" or kind == category
            if requested and folded in candidate.casefold():
                line = source.splitlines()[node.start_point.row].decode("utf-8", errors="replace")
                matches.append(
                    {
                        "path": str(path.relative_to(self._policy.workspace)).replace("\\", "/"),
                        "line": node.start_point.row + 1,
                        "kind": category,
                        "language": language,
                        "name": candidate[:200],
                        "snippet": line.strip()[:300],
                    }
                )
            stack.extend(reversed(node.named_children))
        return matches

    def _text_matches(
        self, path: Path, source: bytes, query: str, kind: SymbolKind
    ) -> list[dict[str, object]]:
        """Fall back to bounded literal line matches for unsupported grammars."""
        if kind not in {"any", "reference"}:
            return []
        matches: list[dict[str, object]] = []
        for line_number, line in enumerate(source.decode("utf-8").splitlines(), start=1):
            if query.casefold() not in line.casefold():
                continue
            matches.append(
                {
                    "path": str(path.relative_to(self._policy.workspace)).replace("\\", "/"),
                    "line": line_number,
                    "kind": "reference",
                    "language": "text",
                    "name": query[:200],
                    "snippet": line.strip()[:300],
                }
            )
        return matches


def _node_category(node: Node) -> SymbolKind | Literal["other"]:
    lowered = node.type.casefold()
    if lowered in _DEFINITION_TYPES:
        return "definition"
    if "import" in lowered or lowered in {"using_directive", "include_statement"}:
        return "import"
    if lowered in {"identifier", "type_identifier", "property_identifier"}:
        return "reference"
    return "other"


def _candidate_text(node: Node, source: bytes, category: str) -> str:
    if category == "definition":
        name = node.child_by_field_name("name")
        if name is not None:
            return source[name.start_byte : name.end_byte].decode("utf-8", errors="replace")
    text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    return text[:500]


def _normalize_language(language: str) -> str:
    """Normalize friendly language labels to Tree-sitter grammar names."""
    normalized = language.strip().casefold().replace(" ", "")
    aliases = {
        "c#": "csharp",
        "c++": "cpp",
        "typescript/tsx": "typescript",
        "powershell": "powershell",
    }
    return aliases.get(normalized, normalized)
