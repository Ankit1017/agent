"""Terminal capability detection and interface-mode selection."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal, TextIO

UiMode = Literal["auto", "tui", "plain"]
ResolvedUiMode = Literal["tui", "plain"]


def select_ui_mode(
    requested: UiMode,
    stdin: TextIO,
    stdout: TextIO,
    environment: Mapping[str, str] | None = None,
) -> ResolvedUiMode:
    """Resolve an explicit or automatic UI choice from terminal capabilities."""
    if requested != "auto":
        return requested
    values = os.environ if environment is None else environment
    if values.get("NO_COLOR") is not None or values.get("TERM", "").casefold() == "dumb":
        return "plain"
    if not stdin.isatty() or not stdout.isatty():
        return "plain"
    return "tui"
