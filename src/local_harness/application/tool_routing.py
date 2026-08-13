"""Deterministic request-scoped tool profiles and deferred discovery."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from local_harness.application.ports import Tool
from local_harness.domain.models import ToolDefinition, ToolResult

ToolProfile = Literal["auto", "coding", "web", "system", "general"]


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """Compact searchable metadata for one registered tool."""

    name: str
    description: str
    profile: Literal["coding", "web", "system", "general"]
    risk: Literal["read", "approval", "trusted"]
    keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolSelection:
    """Initial request profile and selected tool names."""

    profile: Literal["coding", "web", "system", "general"]
    names: tuple[str, ...]
    catalog_schema_chars: int
    selected_schema_chars: int


class RequestToolRouter:
    """Expose a bounded schema set and expand it through deterministic discovery."""

    def __init__(
        self,
        tools: Sequence[Tool],
        *,
        configured_profile: ToolProfile = "auto",
        schema_limit: int = 8,
        activation_limit: int = 5,
    ) -> None:
        """Create a request-scoped router over a complete tool collection."""
        if not 1 <= schema_limit <= 32:
            raise ValueError("tool schema limit must be between 1 and 32")
        if not 1 <= activation_limit <= schema_limit:
            raise ValueError("tool activation limit must fit the schema limit")
        self._tools = {tool.definition.name: tool for tool in tools}
        self._configured_profile = configured_profile
        self._schema_limit = schema_limit
        self._activation_limit = activation_limit
        self._active: list[str] = []
        self._profile: Literal["coding", "web", "system", "general"] = "general"
        self._workflow_stage_tools: tuple[str, ...] = ()
        self._workflow_allowed_tools: frozenset[str] | None = None
        self._tools["discover_tools"] = self
        self._descriptors = tuple(_descriptor(tool) for tool in self._tools.values())

    @property
    def definition(self) -> ToolDefinition:
        """Return the compact deferred-discovery schema."""
        return ToolDefinition(
            "discover_tools",
            "Find and activate tools relevant to a capability or task.",
            {
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1}},
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Activate bounded matching tools and return their descriptors."""
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult("query must be a non-empty string", True)
        matches = self.discover(query)
        payload = {
            "version": 1,
            "summary": f"Activated {len(matches)} matching tool(s)",
            "items": [
                {
                    "name": item.name,
                    "description": item.description,
                    "profile": item.profile,
                    "risk": item.risk,
                }
                for item in matches
            ],
            "truncated": False,
            "next_cursor": None,
            "metadata": {"active_tools": list(self.active_names)},
        }
        return ToolResult(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    @property
    def active_names(self) -> tuple[str, ...]:
        """Return names currently exposed to the provider."""
        return tuple(self._active)

    @property
    def profile(self) -> str:
        """Return the effective request profile."""
        return self._profile

    def start(self, prompt: str) -> ToolSelection:
        """Select a deterministic initial profile for a sanitized request."""
        self._profile = _select_profile(prompt, self._configured_profile)
        self._workflow_stage_tools = ()
        self._workflow_allowed_tools = None
        ranked = sorted(
            self._descriptors,
            key=lambda item: (
                item.name != "discover_tools",
                item.profile != self._profile,
                -_score(item, prompt),
                item.name,
            ),
        )
        preferred = [
            item.name for item in ranked if _initially_relevant(item, prompt, self._profile)
        ]
        if "discover_tools" in self._tools and "discover_tools" not in preferred:
            preferred.insert(0, "discover_tools")
        self._active = preferred[: self._schema_limit]
        all_size = _schema_chars([tool.definition for tool in self._tools.values()])
        selected_size = _schema_chars(self.definitions())
        return ToolSelection(self._profile, tuple(self._active), all_size, selected_size)

    def discover(self, query: str) -> tuple[ToolDescriptor, ...]:
        """Activate the strongest matching tools and return compact descriptors."""
        folded = query.strip().casefold()
        if not folded:
            return ()
        ranked = sorted(
            self._descriptors,
            key=lambda item: (-_score(item, folded), item.name),
        )
        matches = [
            item
            for item in ranked
            if _score(item, folded) > 0
            and (
                self._workflow_allowed_tools is None
                or item.name in self._workflow_allowed_tools
                or item.name == "discover_tools"
            )
        ][: self._activation_limit]
        requested = [item.name for item in matches]
        retained = [name for name in self._active if name not in requested]
        pinned = [name for name in retained if name == "discover_tools"]
        others = [name for name in retained if name != "discover_tools"]
        self._active = [*pinned, *requested, *others][: self._schema_limit]
        return tuple(matches)

    def definitions(self) -> list[ToolDefinition]:
        """Return active schemas in stable selected order."""
        return [self._tools[name].definition for name in self._active if name in self._tools]

    def is_active(self, name: str) -> bool:
        """Return whether a tool can execute during the current request."""
        return name in self._active

    def set_workflow_stage(self, stage_tools: Sequence[str], allowed_tools: Sequence[str]) -> None:
        """Constrain schemas to the selected workflow stage and workflow allowlist."""
        self._workflow_stage_tools = tuple(
            name for name in dict.fromkeys(stage_tools) if name in self._tools
        )
        self._workflow_allowed_tools = frozenset(allowed_tools)
        preferred = ["discover_tools", *self._workflow_stage_tools]
        self._active = list(dict.fromkeys(preferred))[: self._schema_limit]

    def clear_workflow(self) -> None:
        """Remove workflow constraints without changing the current profile selection."""
        self._workflow_stage_tools = ()
        self._workflow_allowed_tools = None

    def catalog(self, query: str = "") -> tuple[ToolDescriptor, ...]:
        """Return all or matching compact descriptors without activation."""
        if not query.strip():
            return self._descriptors
        return tuple(item for item in self._descriptors if _score(item, query) > 0)


def _select_profile(
    prompt: str, configured: ToolProfile
) -> Literal["coding", "web", "system", "general"]:
    if configured != "auto":
        return configured
    words = set(_tokens(prompt))
    if words & {"latest", "current", "web", "online", "news", "research"}:
        return "web"
    if words & {"powershell", "terminal", "process", "service", "windows", "command"}:
        return "system"
    if words & {
        "code",
        "project",
        "test",
        "bug",
        "fix",
        "implement",
        "git",
        "diff",
        "function",
        "class",
        "build",
        "refactor",
        "review",
        "change",
        "changes",
        "failing",
        "authentication",
        "auth",
        "dependency",
        "dependencies",
        "upgrade",
    }:
        return "coding"
    return "general"


def _initially_relevant(item: ToolDescriptor, prompt: str, profile: str) -> bool:
    if item.name == "discover_tools":
        return True
    base = {
        "coding": {"project_memory", "read_symbol", "changed_context", "task_plan"},
        "web": {"web_search", "read_web_pages", "inspect_project"},
        "system": {"list_directory", "read_file", "search_text", "run_powershell"},
        "general": {"inspect_project", "read_files", "list_directory"},
    }[profile]
    if item.name in base:
        return True
    return item.profile == profile and _score(item, prompt) > 0


def _descriptor(tool: Tool) -> ToolDescriptor:
    name = tool.definition.name
    profile: Literal["coding", "web", "system", "general"]
    if name in {"web_search", "read_web_pages"}:
        profile = "web"
    elif name in {"run_powershell", "list_directory", "read_file", "search_text"}:
        profile = "system"
    elif name in {
        "inspect_project",
        "find_code",
        "read_files",
        "apply_patch",
        "run_project_checks",
        "git_inspect",
        "code_intelligence",
        "task_plan",
        "project_memory",
        "read_symbol",
        "changed_context",
        "dependency_context",
    }:
        profile = "coding"
    else:
        profile = "general"
    risk: Literal["read", "approval", "trusted"] = (
        "approval" if name in {"run_powershell", "apply_patch", "run_project_checks"} else "read"
    )
    text = f"{name} {tool.definition.description}"
    return ToolDescriptor(
        name, tool.definition.description[:240], profile, risk, tuple(_tokens(text))
    )


def _score(item: ToolDescriptor, query: str) -> int:
    words = set(_tokens(query))
    if not words:
        return 0
    name_words = set(_tokens(item.name))
    return len(words & set(item.keywords)) + 3 * len(words & name_words)


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold().replace("_", " "))


def _schema_chars(definitions: Sequence[ToolDefinition]) -> int:
    return len(json.dumps([definition.parameters for definition in definitions], sort_keys=True))
