"""Compact project discovery, batch reading, and check-profile detection."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from local_harness.domain.errors import ToolExecutionError
from local_harness.guardrails.path_policy import WorkspacePathPolicy

_IGNORED_DIRECTORIES = frozenset(
    {".venv", "venv", "node_modules", "dist", "build", "target", "__pycache__"}
)
_MANIFESTS = frozenset(
    {
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "global.json",
    }
)
_ENTRYPOINTS = frozenset(
    {
        "main.py",
        "app.py",
        "__main__.py",
        "index.js",
        "index.ts",
        "main.go",
        "main.rs",
        "program.cs",
    }
)
_LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C/C++",
    ".cc": "C++",
    ".cpp": "C++",
    ".hpp": "C++",
    ".sh": "Bash",
    ".ps1": "PowerShell",
    ".json": "JSON",
    ".html": "HTML",
    ".css": "CSS",
    ".md": "Markdown",
    ".yaml": "YAML",
    ".yml": "YAML",
}


class ProjectInspector:
    """Discover bounded project structure and known verification commands."""

    def __init__(
        self,
        policy: WorkspacePathPolicy,
        *,
        max_entries: int = 300,
        python_lsp_command: str = "",
        typescript_lsp_command: str = "",
    ) -> None:
        """Bind project discovery to a guarded workspace."""
        self._policy = policy
        self._max_entries = max_entries
        self._python_lsp_command = python_lsp_command or _first_available(
            "basedpyright-langserver", "pyright-langserver"
        )
        self._typescript_lsp_command = typescript_lsp_command or _first_available(
            "typescript-language-server"
        )

    def inspect(self, requested_path: str, depth: int) -> dict[str, object]:
        """Return deterministic project metadata without executing project code."""
        if not 1 <= depth <= 5:
            raise ToolExecutionError("depth must be between 1 and 5")
        root = self._policy.resolve(requested_path)
        if not root.is_dir():
            raise ToolExecutionError(f"Not a directory: {requested_path}")
        tree: list[str] = []
        manifests: list[str] = []
        entrypoints: list[str] = []
        language_counts: dict[str, int] = {}
        truncated = False
        for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
            relative = path.relative_to(root)
            if len(relative.parts) > depth or self._skip(path, relative):
                continue
            if len(tree) >= self._max_entries:
                truncated = True
                break
            display = str(relative).replace("\\", "/") + ("/" if path.is_dir() else "")
            tree.append(display)
            if not path.is_file():
                continue
            lowered = path.name.casefold()
            if lowered in _MANIFESTS:
                manifests.append(display)
            if lowered in _ENTRYPOINTS:
                entrypoints.append(display)
            language = _LANGUAGES.get(path.suffix.casefold())
            if language:
                language_counts[language] = language_counts.get(language, 0) + 1
        relative_root = str(root.relative_to(self._policy.workspace))
        profiles = CheckProfileDetector(self._policy).detect(relative_root)
        return {
            "path": str(root.relative_to(self._policy.workspace)) or ".",
            "languages": [
                {"name": name, "files": count}
                for name, count in sorted(
                    language_counts.items(), key=lambda item: (-item[1], item[0])
                )
            ],
            "frameworks": self._frameworks(root),
            "manifests": manifests,
            "entrypoints": entrypoints,
            "tree": tree,
            "check_profiles": [
                {"name": name, "command": command} for name, command in profiles.items()
            ],
            "git": {
                "available": bool(shutil.which("git")),
                "repository": (root / ".git").exists(),
            },
            "language_servers": {
                "python": self._python_lsp_command or None,
                "typescript": self._typescript_lsp_command or None,
            },
            "recommended_tool_profile": "coding",
            "truncated": truncated,
        }

    def _skip(self, path: Path, relative: Path) -> bool:
        return self._policy.is_protected(path) or any(
            part.casefold() in _IGNORED_DIRECTORIES for part in relative.parts
        )

    @staticmethod
    def _frameworks(root: Path) -> list[str]:
        markers: list[str] = []
        pyproject = root / "pyproject.toml"
        package = root / "package.json"
        for path in (pyproject, package):
            if not path.is_file() or path.stat().st_size > 200_000:
                continue
            try:
                text = path.read_text(encoding="utf-8").casefold()
            except (OSError, UnicodeDecodeError):
                continue
            candidates = {
                "fastapi": "FastAPI",
                "django": "Django",
                "flask": "Flask",
                '"react"': "React",
                '"next"': "Next.js",
                '"vue"': "Vue",
                '"angular"': "Angular",
                '"svelte"': "Svelte",
            }
            markers.extend(label for token, label in candidates.items() if token in text)
        return sorted(set(markers))


class BatchFileReader:
    """Read several guarded UTF-8 ranges under one shared request limit."""

    def __init__(self, policy: WorkspacePathPolicy, *, max_file_bytes: int = 1_000_000) -> None:
        """Bind batch reads to workspace and file-size policies."""
        self._policy = policy
        self._max_file_bytes = max_file_bytes

    def read(self, requested_path: str, start_line: int, end_line: int) -> dict[str, object]:
        """Read one range and return content with an integrity hash."""
        if start_line < 1 or end_line < start_line or end_line - start_line + 1 > 500:
            raise ToolExecutionError("line range must contain between 1 and 500 lines")
        path = self._policy.resolve(requested_path, allow_root=False)
        if not path.is_file():
            raise ToolExecutionError(f"Not a file: {requested_path}")
        raw = path.read_bytes()
        if len(raw) > self._max_file_bytes:
            raise ToolExecutionError("File exceeds the inspection byte limit")
        if b"\x00" in raw[:8192]:
            raise ToolExecutionError("Binary files cannot be read")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolExecutionError("File is not valid UTF-8 text") from exc
        lines = text.splitlines()
        selected = lines[start_line - 1 : end_line]
        return {
            "path": str(path.relative_to(self._policy.workspace)).replace("\\", "/"),
            "start_line": start_line,
            "end_line": start_line + max(0, len(selected) - 1),
            "content": "\n".join(
                f"{number}: {line}" for number, line in enumerate(selected, start=start_line)
            ),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }


class CheckProfileDetector:
    """Detect non-fixing project verification commands from known manifests."""

    def __init__(self, policy: WorkspacePathPolicy) -> None:
        """Bind profile detection to a guarded workspace."""
        self._policy = policy

    def detect(self, requested_path: str) -> dict[str, str]:
        """Return stable profile names and exact PowerShell commands."""
        root = self._policy.resolve(requested_path)
        if not root.is_dir():
            raise ToolExecutionError(f"Not a directory: {requested_path}")
        prefix = _powershell_location(root, self._policy.workspace)
        profiles: dict[str, str] = {}
        if (root / "scripts" / "check.ps1").is_file():
            profiles["quality"] = f"{prefix}& .\\scripts\\check.ps1"
        if (root / "pyproject.toml").is_file():
            profiles.setdefault("tests", f"{prefix}python -m pytest")
            profiles.setdefault("lint", f"{prefix}python -m ruff check .")
            profiles.setdefault("typecheck", f"{prefix}python -m mypy .")
        if (root / "package.json").is_file():
            for name in _package_scripts(root / "package.json"):
                if name in {"test", "lint", "typecheck", "check", "build"}:
                    profiles.setdefault(name, f"{prefix}npm run {name}")
        if (root / "Cargo.toml").is_file():
            profiles.setdefault("check", f"{prefix}cargo check")
            profiles.setdefault("tests", f"{prefix}cargo test")
        if (root / "go.mod").is_file():
            profiles.setdefault("tests", f"{prefix}go test ./...")
        if any(root.glob("*.sln")) or any(root.glob("*.csproj")):
            profiles.setdefault("build", f"{prefix}dotnet build --no-restore")
            profiles.setdefault("tests", f"{prefix}dotnet test --no-restore")
        return profiles


def _package_scripts(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set()
    scripts = payload.get("scripts", {}) if isinstance(payload, dict) else {}
    return set(scripts) if isinstance(scripts, dict) else set()


def _powershell_location(root: Path, workspace: Path) -> str:
    relative = root.relative_to(workspace)
    if not relative.parts:
        return ""
    escaped = str(relative).replace("'", "''")
    return f"Set-Location -LiteralPath '{escaped}'; "


def _first_available(*commands: str) -> str:
    return next((command for command in commands if shutil.which(command)), "")
