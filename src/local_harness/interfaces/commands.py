"""Pure parsing for commands shared by terminal interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CommandName = Literal[
    "help",
    "new",
    "sessions",
    "events",
    "max-turns",
    "resume",
    "exit",
    "session-info",
    "summarize",
    "quota",
    "tag",
    "tags",
    "export",
    "archive",
    "archives",
    "restore",
    "session-check",
    "plugins",
    "tools",
    "plan",
    "index",
    "memory",
    "workflows",
    "workflow",
    "eval",
    "handoff",
    "candidate",
    "model",
    "models",
]


@dataclass(frozen=True, slots=True)
class InterfaceCommand:
    """A recognized interface command and its unprocessed argument."""

    name: CommandName
    argument: str = ""


@dataclass(frozen=True, slots=True)
class CommandParseResult:
    """The result of parsing user input without performing side effects."""

    command: InterfaceCommand | None = None
    error: str = ""


def parse_command(value: str) -> CommandParseResult:
    """Parse a slash command, returning no command for ordinary prompts."""
    stripped = value.strip()
    if not stripped.startswith("/"):
        return CommandParseResult()
    head, _, argument = stripped[1:].partition(" ")
    known: tuple[CommandName, ...] = (
        "help",
        "new",
        "sessions",
        "events",
        "max-turns",
        "resume",
        "exit",
        "session-info",
        "summarize",
        "quota",
        "tag",
        "tags",
        "export",
        "archive",
        "archives",
        "restore",
        "session-check",
        "plugins",
        "tools",
        "plan",
        "index",
        "memory",
        "workflows",
        "workflow",
        "eval",
        "handoff",
        "candidate",
        "model",
        "models",
    )
    if head not in known:
        return CommandParseResult(error="Unknown command. Type /help.")
    name = head  # narrowed by the membership check above
    if (
        name
        in {
            "help",
            "new",
            "sessions",
            "exit",
            "archives",
            "plugins",
            "plan",
            "handoff",
            "models",
        }
        and argument.strip()
    ):
        return CommandParseResult(error=f"/{name} does not accept an argument.")
    if name == "resume" and not argument.strip():
        return CommandParseResult(error="Usage: /resume <session-id>")
    if name == "memory" and not argument.strip():
        return CommandParseResult(error="Usage: /memory <query>")
    if name in {"archive", "restore", "export", "tag"} and not argument.strip():
        return CommandParseResult(error=f"Usage: /{name} requires arguments")
    return CommandParseResult(InterfaceCommand(name, argument.strip()))
