"""Bounded, read-only filesystem inspection adapter."""

from __future__ import annotations

import fnmatch

from local_harness.domain.errors import ToolExecutionError
from local_harness.guardrails.path_policy import WorkspacePathPolicy
from local_harness.text import truncate_text


class WorkspaceInspector:
    """Inspect text files under one guarded workspace."""

    def __init__(
        self,
        policy: WorkspacePathPolicy,
        *,
        max_output_chars: int,
        max_entries: int = 200,
        max_matches: int = 100,
        max_files_searched: int = 2_000,
        max_file_bytes: int = 1_000_000,
    ) -> None:
        """Configure deterministic inspection limits."""
        self._policy = policy
        self._max_output_chars = max_output_chars
        self._max_entries = max_entries
        self._max_matches = max_matches
        self._max_files_searched = max_files_searched
        self._max_file_bytes = max_file_bytes

    def list_directory(self, requested_path: str) -> str:
        """List one directory without recursively expanding it."""
        path = self._policy.resolve(requested_path)
        if not path.is_dir():
            raise ToolExecutionError(f"Not a directory: {requested_path}")
        entries: list[str] = []
        visible = sorted(
            (child for child in path.iterdir() if not self._policy.is_protected(child)),
            key=lambda item: (not item.is_dir(), item.name.lower()),
        )
        for child in visible[: self._max_entries]:
            suffix = "/" if child.is_dir() else ""
            entries.append(f"{child.relative_to(self._policy.workspace)}{suffix}")
        if len(visible) > self._max_entries:
            entries.append(f"...[{len(visible) - self._max_entries} entries omitted]")
        text, _ = truncate_text("\n".join(entries) or "(empty directory)", self._max_output_chars)
        return text

    def read_file(self, requested_path: str, start_line: int = 1, end_line: int = 400) -> str:
        """Read a bounded inclusive line range from one UTF-8 text file."""
        if start_line < 1 or end_line < start_line:
            raise ToolExecutionError("Line range must satisfy 1 <= start_line <= end_line")
        path = self._policy.resolve(requested_path, allow_root=False)
        if not path.is_file():
            raise ToolExecutionError(f"Not a file: {requested_path}")
        if path.stat().st_size > self._max_file_bytes:
            raise ToolExecutionError(
                f"File exceeds the {self._max_file_bytes}-byte inspection limit"
            )
        raw = path.read_bytes()
        if b"\x00" in raw[:8192]:
            raise ToolExecutionError("Binary files cannot be read")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolExecutionError("File is not valid UTF-8 text") from exc
        selected = text.splitlines()[start_line - 1 : min(end_line, start_line + 999)]
        numbered = "\n".join(
            f"{number}: {line}" for number, line in enumerate(selected, start=start_line)
        )
        bounded, _ = truncate_text(
            numbered or "(no lines in requested range)", self._max_output_chars
        )
        return bounded

    def search_text(self, query: str, requested_path: str, file_pattern: str) -> str:
        """Search UTF-8 workspace files using a literal case-insensitive query."""
        if not query:
            raise ToolExecutionError("Search query cannot be empty")
        root = self._policy.resolve(requested_path)
        if not root.is_dir():
            raise ToolExecutionError(f"Not a directory: {requested_path}")
        matches: list[str] = []
        files_seen = 0
        for path in root.rglob("*"):
            if files_seen >= self._max_files_searched or len(matches) >= self._max_matches:
                break
            if not path.is_file() or self._policy.is_protected(path):
                continue
            files_seen += 1
            if not fnmatch.fnmatch(path.name, file_pattern):
                continue
            if path.stat().st_size > self._max_file_bytes:
                continue
            try:
                raw = path.read_bytes()
                if b"\x00" in raw[:8192]:
                    continue
                lines = raw.decode("utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(lines, start=1):
                if query.casefold() in line.casefold():
                    relative = path.relative_to(self._policy.workspace)
                    matches.append(f"{relative}:{number}: {line}")
                    if len(matches) >= self._max_matches:
                        break
        suffix = ""
        if files_seen >= self._max_files_searched or len(matches) >= self._max_matches:
            suffix = "\n...[search limit reached]"
        result, _ = truncate_text("\n".join(matches) + suffix, self._max_output_chars)
        return result or "(no matches)"
