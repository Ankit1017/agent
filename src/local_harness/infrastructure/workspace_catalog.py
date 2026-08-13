"""Atomic JSON persistence for browser workspace registrations."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from local_harness.domain.errors import SessionError
from local_harness.domain.web_ui import WorkspaceEntry
from local_harness.guardrails.workspace_catalog_policy import WorkspaceCatalogPolicy
from local_harness.identifiers import new_session_id


class JsonWorkspaceCatalog:
    """Persist a small allowlist of canonical workspace roots."""

    def __init__(self, path: Path, control_workspace: Path) -> None:
        """Initialize the catalog and guarantee one immutable control entry."""
        self._path = path
        self._policy = WorkspaceCatalogPolicy()
        self._control = control_workspace.resolve(strict=True)
        if not path.exists():
            self._entries = [self._new_entry(self._control.name or "Harness", self._control, True)]
            self._save()
        else:
            self._entries = self._load()
            if not any(item.is_control for item in self._entries):
                self._entries.insert(
                    0, self._new_entry(self._control.name or "Harness", self._control, True)
                )
                self._save()

    def list_entries(self) -> list[WorkspaceEntry]:
        """Return registered workspaces in stable order."""
        return list(self._entries)

    def get(self, workspace_id: str) -> WorkspaceEntry:
        """Return one registered workspace."""
        for entry in self._entries:
            if entry.workspace_id == workspace_id:
                return entry
        raise SessionError("Workspace is not registered")

    def validate(self, label: str, raw_path: str) -> tuple[str, Path]:
        """Validate a proposed label and path without changing the catalog."""
        clean_label = " ".join(label.split())
        if not 1 <= len(clean_label) <= 80:
            raise SessionError("Workspace label must contain 1 to 80 characters")
        resolved = self._policy.resolve(raw_path)
        if any(Path(item.path) == resolved for item in self._entries):
            raise SessionError("Workspace is already registered")
        return clean_label, resolved

    def add(self, label: str, resolved_path: Path) -> WorkspaceEntry:
        """Persist a previously validated workspace."""
        clean_label, resolved = self.validate(label, str(resolved_path))
        entry = self._new_entry(clean_label, resolved, False)
        self._entries.append(entry)
        self._save()
        return entry

    def remove(self, workspace_id: str) -> WorkspaceEntry:
        """Remove only a non-control catalog entry without touching workspace data."""
        entry = self.get(workspace_id)
        if entry.is_control:
            raise SessionError("The control workspace cannot be removed")
        self._entries = [item for item in self._entries if item.workspace_id != workspace_id]
        self._save()
        return entry

    def _new_entry(self, label: str, path: Path, control: bool) -> WorkspaceEntry:
        return WorkspaceEntry(new_session_id(), label, str(path), is_control=control)

    def _load(self) -> list[WorkspaceEntry]:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != 1 or not isinstance(
                payload.get("workspaces"), list
            ):
                raise ValueError
            entries = [WorkspaceEntry(**item) for item in payload["workspaces"]]
            if len({item.workspace_id for item in entries}) != len(entries):
                raise ValueError
            return entries
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise SessionError("Workspace catalog is malformed or unreadable") from exc

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "workspaces": [asdict(item) for item in self._entries]}
        descriptor, temporary = tempfile.mkstemp(
            dir=self._path.parent, prefix="workspace-catalog-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
