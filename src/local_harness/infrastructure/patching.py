"""Validated, approved, transactional workspace text patches."""

from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from local_harness.application.ports import PatchApprovalGateway
from local_harness.domain.errors import HarnessError, ToolExecutionError
from local_harness.domain.models import ToolResult
from local_harness.guardrails.path_policy import WorkspacePathPolicy
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.tool_output import tool_envelope


class WorkspacePatchService:
    """Validate, preview, approve, and apply one multi-file patch transaction."""

    def __init__(
        self,
        policy: WorkspacePathPolicy,
        approval: PatchApprovalGateway,
        redactor: SecretRedactor,
        *,
        max_patch_chars: int,
        max_output_chars: int,
    ) -> None:
        """Configure patch safety, approval, and output limits."""
        self._policy = policy
        self._approval = approval
        self._redactor = redactor
        self._max_patch_chars = max_patch_chars
        self._max_output_chars = max_output_chars

    def apply(self, changes: Sequence[Mapping[str, object]], explanation: str) -> ToolResult:
        """Apply an approved transaction or return a safe rejected result."""
        try:
            transaction = self._validate(changes)
        except (HarnessError, OSError, UnicodeDecodeError) as exc:
            return ToolResult(str(exc), True)
        preview = _preview(transaction, self._policy.workspace)
        if len(preview) > self._max_patch_chars:
            return ToolResult("Patch preview exceeds HARNESS_PATCH_MAX_CHARS", True)
        decision = self._approval.request_patch(
            self._redactor.redact(preview),
            self._redactor.redact(explanation),
            str(self._policy.workspace),
        )
        if not decision.approved:
            feedback = self._redactor.redact(decision.feedback or "No reason supplied")
            return ToolResult(f"User rejected the patch. Feedback: {feedback}", True)
        try:
            self._commit(transaction)
        except (OSError, ToolExecutionError) as exc:
            return ToolResult(f"Patch transaction failed and was rolled back: {exc}", True)
        items = [
            {
                "path": str(path.relative_to(self._policy.workspace)).replace("\\", "/"),
                "action": (
                    "delete" if updated is None else "create" if original is None else "update"
                ),
                "sha256": hashlib.sha256(updated or b"").hexdigest(),
            }
            for path, (original, updated) in transaction.items()
        ]
        return ToolResult(
            tool_envelope(
                f"Applied {len(items)} file change(s)",
                items,
                max_chars=self._max_output_chars,
                redactor=self._redactor,
            )
        )

    def _validate(
        self, changes: Sequence[Mapping[str, object]]
    ) -> dict[Path, tuple[bytes | None, bytes | None]]:
        if not 1 <= len(changes) <= 20:
            raise ToolExecutionError("changes must contain between 1 and 20 operations")
        states: dict[Path, tuple[bytes | None, bytes | None]] = {}
        actions: dict[Path, set[str]] = {}
        for change in changes:
            action = _required_string(change, "action")
            raw_path = _required_string(change, "path")
            if Path(raw_path).is_absolute():
                raise ToolExecutionError("Patch paths must be workspace-relative")
            path = self._policy.resolve(raw_path, allow_root=False)
            _reject_symlink_path(self._policy.workspace, raw_path)
            actions.setdefault(path, set()).add(action)
            if len(actions) > 10:
                raise ToolExecutionError("A patch may affect at most 10 files")
            if len(actions[path]) > 1 and action in {"create", "delete"}:
                raise ToolExecutionError(f"Conflicting operations for {raw_path}")
            if path not in states:
                original = path.read_bytes() if path.exists() else None
                if original is not None:
                    _decode_text(original, raw_path)
                states[path] = (original, original)
            original, current = states[path]
            if action == "create":
                if current is not None:
                    raise ToolExecutionError(f"Create target already exists: {raw_path}")
                content = _required_string(change, "content")
                states[path] = (original, content.encode("utf-8"))
            elif action == "replace":
                if current is None:
                    raise ToolExecutionError(f"Replace target does not exist: {raw_path}")
                text = _decode_text(current, raw_path)
                old_text = _required_string(change, "old_text")
                new_text = _required_string(change, "new_text")
                if not old_text:
                    raise ToolExecutionError("old_text cannot be empty")
                if text.count(old_text) != 1:
                    raise ToolExecutionError(f"old_text must match exactly once in {raw_path}")
                states[path] = (original, text.replace(old_text, new_text, 1).encode("utf-8"))
            elif action == "delete":
                if current is None:
                    raise ToolExecutionError(f"Delete target does not exist: {raw_path}")
                expected = _required_string(change, "expected_sha256")
                if hashlib.sha256(current).hexdigest() != expected:
                    raise ToolExecutionError(f"SHA-256 does not match for {raw_path}")
                states[path] = (original, None)
            else:
                raise ToolExecutionError("action must be create, replace, or delete")
        total = sum(len(updated or b"") for _, updated in states.values())
        if total > self._max_patch_chars:
            raise ToolExecutionError("Patch content exceeds HARNESS_PATCH_MAX_CHARS")
        return states

    def _commit(self, transaction: dict[Path, tuple[bytes | None, bytes | None]]) -> None:
        for path, (original, _) in transaction.items():
            current = path.read_bytes() if path.exists() else None
            if current != original:
                raise ToolExecutionError(f"File changed after validation: {path.name}")
        try:
            for path, (_, updated) in transaction.items():
                if updated is None:
                    path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_write(path, updated)
        except OSError:
            for path, (original, _) in transaction.items():
                try:
                    if original is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        _atomic_write(path, original)
                except OSError:
                    pass
            raise


def _preview(transaction: Mapping[Path, tuple[bytes | None, bytes | None]], workspace: Path) -> str:
    sections: list[str] = []
    for path, (original, updated) in transaction.items():
        before = _decode_text(original or b"", str(path)).splitlines(keepends=True)
        after = _decode_text(updated or b"", str(path)).splitlines(keepends=True)
        relative = str(path.relative_to(workspace)).replace("\\", "/")
        sections.extend(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{relative}" if original is not None else "/dev/null",
                tofile=f"b/{relative}" if updated is not None else "/dev/null",
            )
        )
    return "".join(sections) or "(no textual changes)"


def _atomic_write(path: Path, content: bytes) -> None:
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def _reject_symlink_path(workspace: Path, requested_path: str) -> None:
    current = workspace
    for part in Path(requested_path).parts:
        current /= part
        if current.exists() and current.is_symlink():
            raise ToolExecutionError("Patch paths cannot contain symbolic links")


def _required_string(values: Mapping[str, object], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str):
        raise ToolExecutionError(f"{name} must be a string")
    return value


def _decode_text(content: bytes, path: str) -> str:
    if b"\x00" in content[:8192]:
        raise ToolExecutionError(f"Binary file cannot be patched: {path}")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolExecutionError(f"File is not UTF-8: {path}") from exc
