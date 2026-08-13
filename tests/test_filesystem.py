"""Tests for bounded workspace inspection."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_harness.domain.errors import ToolExecutionError
from local_harness.guardrails.path_policy import WorkspacePathPolicy
from local_harness.infrastructure.filesystem import WorkspaceInspector


def _inspector(workspace: Path, **limits: int) -> WorkspaceInspector:
    return WorkspaceInspector(
        WorkspacePathPolicy(workspace),
        max_output_chars=limits.pop("max_output_chars", 500),
        **limits,
    )


def test_list_directory_hides_protected_entries_and_limits_results(tmp_path: Path) -> None:
    """Listings are sorted, bounded, and exclude protected directories."""
    (tmp_path / "folder").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")

    result = _inspector(tmp_path, max_entries=2).list_directory(".")

    assert "folder/" in result
    assert ".git" not in result
    assert "entries omitted" in result
    with pytest.raises(ToolExecutionError):
        _inspector(tmp_path).list_directory("missing")


def test_read_file_numbers_lines_and_rejects_invalid_inputs(tmp_path: Path) -> None:
    """Text reads respect ranges and reject binary or invalid requests."""
    (tmp_path / "text.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"abc\x00def")
    (tmp_path / "latin.txt").write_bytes(b"\xff")
    (tmp_path / "large.txt").write_text("too large", encoding="utf-8")

    assert _inspector(tmp_path).read_file("text.txt", 2, 3) == "2: two\n3: three"
    with pytest.raises(ToolExecutionError):
        _inspector(tmp_path).read_file("text.txt", 0, 2)
    with pytest.raises(ToolExecutionError):
        _inspector(tmp_path).read_file("missing.txt")
    with pytest.raises(ToolExecutionError):
        _inspector(tmp_path).read_file("binary.bin")
    with pytest.raises(ToolExecutionError):
        _inspector(tmp_path).read_file("latin.txt")
    with pytest.raises(ToolExecutionError, match="exceeds"):
        _inspector(tmp_path, max_file_bytes=2).read_file("large.txt")


def test_search_text_is_literal_case_insensitive_and_bounded(tmp_path: Path) -> None:
    """Search returns locations, honors globs, and reports limits."""
    (tmp_path / "a.py").write_text("First Needle\nneedle again\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle ignored by glob\n", encoding="utf-8")
    (tmp_path / "binary.py").write_bytes(b"needle\x00")
    (tmp_path / "large.py").write_text("needle in large", encoding="utf-8")

    result = _inspector(tmp_path, max_matches=1).search_text("needle", ".", "*.py")

    assert "a.py:1" in result
    assert "search limit reached" in result
    assert _inspector(tmp_path).search_text("absent", ".", "*") == "(no matches)"
    assert "large.py" not in _inspector(tmp_path, max_file_bytes=2).search_text(
        "needle", ".", "large.py"
    )
    with pytest.raises(ToolExecutionError):
        _inspector(tmp_path).search_text("", ".", "*")
    with pytest.raises(ToolExecutionError):
        _inspector(tmp_path).search_text("x", "a.py", "*")
