"""Filesystem adapters for exports, archives, and session integrity."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from local_harness.domain.errors import SessionError
from local_harness.domain.maintenance import ArchiveInfo, ExportResult, IntegrityFinding
from local_harness.domain.models import Session
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.json_sessions import JsonSessionRepository


class SessionFileService:
    """Implement redacted exports, reversible archives, and integrity checks."""

    def __init__(
        self, workspace: Path, repository: JsonSessionRepository, redactor: SecretRedactor
    ) -> None:
        """Bind maintenance operations to one workspace-owned storage tree."""
        self._workspace = workspace.resolve()
        self._repository = repository
        self._redactor = redactor
        self._root = self._workspace / ".harness"
        self._sessions = self._root / "sessions"
        self._archives = self._root / "archive"
        self._exports = self._root / "exports"
        self._corrupt = self._root / "corrupt"
        self._findings: dict[str, tuple[Path, int, int]] = {}

    def export(self, session: Session, format_name: str) -> ExportResult:
        """Write a unique full redacted Markdown or CSV session export."""
        normalized = format_name.casefold()
        if normalized not in {"md", "csv"}:
            raise SessionError("Export format must be md or csv")
        self._exports.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = self._exports / f"{session.session_id}-{stamp}.{normalized}"
        content = (
            self._render_markdown(session) if normalized == "md" else self._render_csv(session)
        )
        self._atomic_write(path, self._redactor.redact(content).encode("utf-8"))
        return ExportResult(str(path), normalized)

    def archive(self, session_id: str) -> ArchiveInfo:
        """Create and verify a compressed archive before removing its source JSON."""
        session = self._repository.load(session_id)
        source = self._sessions / f"{session_id}.json"
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        archived_at = datetime.now(UTC).isoformat()
        manifest = {
            "version": 1,
            "session_id": session_id,
            "schema_version": session.schema_version,
            "workspace": session.workspace,
            "model": session.model,
            "summary": session.summary.text if session.summary else "",
            "archived_at": archived_at,
            "size_bytes": len(data),
            "sha256": digest,
        }
        self._archives.mkdir(parents=True, exist_ok=True)
        zip_path = self._archives / f"{session_id}.zip"
        manifest_path = self._archives / f"{session_id}.manifest.json"
        if zip_path.exists() or manifest_path.exists():
            raise SessionError(f"Session is already archived: {session_id}")
        handle, temporary = tempfile.mkstemp(prefix=f".{session_id}.", dir=self._archives)
        os.close(handle)
        temporary_path = Path(temporary)
        try:
            with zipfile.ZipFile(temporary_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("session.json", data)
            with zipfile.ZipFile(temporary_path, "r") as archive:
                if archive.testzip() is not None or archive.read("session.json") != data:
                    raise SessionError("Session archive verification failed")
            os.replace(temporary_path, zip_path)
            self._atomic_write(
                manifest_path,
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            source.unlink()
        except (OSError, zipfile.BadZipFile) as exc:
            temporary_path.unlink(missing_ok=True)
            if zip_path.exists() and source.exists():
                zip_path.unlink(missing_ok=True)
                manifest_path.unlink(missing_ok=True)
            raise SessionError(f"Could not archive session: {exc}") from exc
        return ArchiveInfo(session_id, archived_at, str(manifest["summary"]), len(data))

    def list_archives(self) -> list[ArchiveInfo]:
        """Return valid archive manifests newest first."""
        if not self._archives.exists():
            return []
        results: list[ArchiveInfo] = []
        for path in self._archives.glob("*.manifest.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                results.append(
                    ArchiveInfo(
                        value["session_id"],
                        value["archived_at"],
                        value.get("summary", ""),
                        value["size_bytes"],
                    )
                )
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return sorted(results, key=lambda item: item.archived_at, reverse=True)

    def restore(self, session_id: str) -> Session:
        """Validate checksum, schema, workspace, and collision before restoration."""
        destination = self._sessions / f"{session_id}.json"
        if destination.exists():
            raise SessionError(f"A live session already exists: {session_id}")
        zip_path = self._archives / f"{session_id}.zip"
        manifest_path = self._archives / f"{session_id}.manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            with zipfile.ZipFile(zip_path, "r") as archive:
                if archive.namelist() != ["session.json"] or archive.testzip() is not None:
                    raise SessionError("Archive contains invalid entries")
                data = archive.read("session.json")
            if hashlib.sha256(data).hexdigest() != manifest["sha256"]:
                raise SessionError("Archive checksum does not match its manifest")
            if Path(manifest["workspace"]).resolve() != self._workspace:
                raise SessionError("Archive belongs to a different workspace")
            self._sessions.mkdir(parents=True, exist_ok=True)
            self._atomic_write(destination, data)
            session = self._repository.load(session_id)
        except (
            OSError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
            zipfile.BadZipFile,
            SessionError,
        ) as exc:
            destination.unlink(missing_ok=True)
            if isinstance(exc, SessionError):
                raise
            raise SessionError(f"Could not restore session: {exc}") from exc
        zip_path.unlink()
        manifest_path.unlink()
        return session

    def scan(self) -> list[IntegrityFinding]:
        """Scan live sessions and archive pairs without modifying them."""
        findings: list[IntegrityFinding] = []
        self._findings.clear()
        if self._sessions.exists():
            for path in self._sessions.glob("*.json"):
                try:
                    self._repository.load(path.stem)
                except SessionError as exc:
                    findings.append(self._finding(path, str(exc)))
        if self._archives.exists():
            stems = {path.name.removesuffix(".zip") for path in self._archives.glob("*.zip")}
            stems.update(
                path.name.removesuffix(".manifest.json")
                for path in self._archives.glob("*.manifest.json")
            )
            for stem in stems:
                zip_path = self._archives / f"{stem}.zip"
                manifest_path = self._archives / f"{stem}.manifest.json"
                if not zip_path.exists() or not manifest_path.exists():
                    path = zip_path if zip_path.exists() else manifest_path
                    findings.append(self._finding(path, "Archive pair is incomplete"))
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    with zipfile.ZipFile(zip_path) as archive:
                        data = archive.read("session.json")
                    if hashlib.sha256(data).hexdigest() != manifest["sha256"]:
                        raise ValueError("checksum mismatch")
                except (
                    OSError,
                    KeyError,
                    ValueError,
                    json.JSONDecodeError,
                    zipfile.BadZipFile,
                ) as exc:
                    findings.append(self._finding(zip_path, f"Archive is corrupt: {exc}"))
        return findings

    def quarantine(self, check_id: str) -> str:
        """Move one unchanged finding into recoverable quarantine storage."""
        record = self._findings.get(check_id)
        if record is None:
            raise SessionError("Integrity finding is missing or stale")
        path, expected_size, expected_mtime = record
        try:
            stat = path.stat()
        except OSError as exc:
            raise SessionError("Integrity finding is missing or stale") from exc
        if stat.st_size != expected_size or stat.st_mtime_ns != expected_mtime:
            raise SessionError("Integrity finding changed after scanning")
        self._corrupt.mkdir(parents=True, exist_ok=True)
        destination = self._corrupt / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{path.name}"
        os.replace(path, destination)
        return str(destination)

    def _finding(self, path: Path, reason: str) -> IntegrityFinding:
        stat = path.stat()
        identity = f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}:{reason}"
        check_id = hashlib.sha256(identity.encode()).hexdigest()[:12]
        self._findings[check_id] = (path, stat.st_size, stat.st_mtime_ns)
        return IntegrityFinding(check_id, path.name, reason, stat.st_size, stat.st_mtime_ns)

    def _render_markdown(self, session: Session) -> str:
        payload = json.dumps(asdict(session), ensure_ascii=False, indent=2)
        summary = session.summary.text if session.summary else "No summary yet"
        return (
            f"# Session {session.session_id}\n\n## Summary\n\n{summary}\n\n"
            f"## Full record\n\n```json\n{payload}\n```\n"
        )

    def _render_csv(self, session: Session) -> str:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["record_type", "sequence", "request", "role_or_kind", "name", "content"])
        for message in session.messages:
            payload = json.dumps(asdict(message), ensure_ascii=False)
            writer.writerow(
                [
                    "message",
                    "",
                    message.request_number or "",
                    message.role,
                    message.name or "",
                    _csv_safe(payload),
                ]
            )
        for event in session.events:
            payload = json.dumps(asdict(event), ensure_ascii=False)
            writer.writerow(
                [
                    "event",
                    event.sequence,
                    event.request_number or "",
                    event.kind,
                    event.target,
                    _csv_safe(payload),
                ]
            )
        for record_type, values in (
            ("workflow", session.workflows),
            ("plan", session.plans),
            ("evidence", session.evidence),
        ):
            for value in values:
                payload = json.dumps(asdict(value), ensure_ascii=False)
                writer.writerow(
                    [
                        record_type,
                        "",
                        value.request_number,
                        "",
                        "",
                        _csv_safe(payload),
                    ]
                )
        return stream.getvalue()

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError:
            Path(temporary).unlink(missing_ok=True)
            raise


def _csv_safe(value: str) -> str:
    stripped = value.lstrip()
    return "'" + value if stripped.startswith(("=", "+", "-", "@")) else value
