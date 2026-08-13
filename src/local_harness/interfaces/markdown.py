"""Terminal-safe assistant Markdown presentation."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import TextIO

from rich.console import Console
from rich.markdown import Markdown

from local_harness.application.answer_quality import normalize_assistant_markdown


def write_assistant_markdown(
    value: str,
    stream: TextIO | None = None,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Render Markdown interactively or emit clean text when styling is unavailable."""
    output = stream or sys.stdout
    env = environment or os.environ
    content = normalize_assistant_markdown(value)
    interactive = output.isatty() and env.get("TERM", "").casefold() != "dumb"
    color_allowed = "NO_COLOR" not in env
    if not interactive:
        output.write(f"{content}\n")
        output.flush()
        return
    console = Console(
        file=output,
        force_terminal=True,
        color_system="standard" if color_allowed else None,
        no_color=not color_allowed,
        soft_wrap=True,
    )
    console.print(Markdown(content, code_theme="ansi_dark"))
