"""Public provider-neutral contract for trusted tool plugins."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PluginContext:
    """Restricted non-secret context supplied to an allowlisted plugin factory."""

    api_version: int
    workspace: str
    max_output_chars: int
