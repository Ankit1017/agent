"""Tests for explicit dependency composition."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_harness.bootstrap import build_runtime


def test_build_runtime_composes_tools_and_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid configuration creates a usable runtime without contacting the model."""
    monkeypatch.setenv("OPENAI_API_KEY", "valid-local-key")

    runtime = build_runtime(tmp_path)
    session = runtime.new_session()
    agent = runtime.agent(session)

    assert runtime.workspace == tmp_path.resolve()
    assert [tool.definition.name for tool in runtime.registry.tools] == [
        "list_directory",
        "read_file",
        "search_text",
        "run_powershell",
        "inspect_project",
        "find_code",
        "read_files",
        "apply_patch",
        "run_project_checks",
        "git_inspect",
        "code_intelligence",
        "web_search",
        "read_web_pages",
        "project_memory",
        "read_symbol",
        "changed_context",
        "dependency_context",
    ]
    assert runtime.sessions.load(session.session_id).session_id == session.session_id
    assert agent.session is session
    assert agent.max_turns == 20


def test_runtime_resolves_cli_and_saved_session_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI overrides saved values, which otherwise override environment settings."""
    monkeypatch.setenv("OPENAI_API_KEY", "valid-local-key")
    monkeypatch.setenv("HARNESS_MAX_TURNS", "25")
    runtime = build_runtime(tmp_path)
    session = runtime.new_session()
    session.max_turns_override = 35
    assert runtime.agent(session).max_turns == 35

    cli_runtime = build_runtime(tmp_path, max_turns_override=45)
    cli_agent = cli_runtime.agent(session)
    assert cli_agent.max_turns == 45
    assert cli_agent.max_turns_source == "CLI"
    assert cli_runtime.sessions.load(session.session_id).max_turns_override == 45

    with pytest.raises(ValueError):
        build_runtime(tmp_path, max_turns_override=101)
