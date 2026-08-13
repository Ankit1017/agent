"""SQLite persistence for workspace-local harness evaluation evidence."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from local_harness.domain.errors import SessionError
from local_harness.domain.evaluation import (
    CandidateComparison,
    EvaluationContract,
    EvaluationObservation,
    EvaluationRun,
    EvaluationScore,
    HandoffSnapshot,
    HarnessCandidate,
)
from local_harness.guardrails.redaction import SecretRedactor

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records (
  kind TEXT NOT NULL,
  record_id TEXT NOT NULL,
  session_id TEXT NOT NULL DEFAULT '',
  request_number INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  payload TEXT NOT NULL,
  PRIMARY KEY(kind, record_id)
);
CREATE INDEX IF NOT EXISTS records_session_request
ON records(kind, session_id, request_number);
"""

type RecordValue = (
    EvaluationContract
    | EvaluationObservation
    | EvaluationRun
    | CandidateComparison
    | HarnessCandidate
    | HandoffSnapshot
)


class SqliteEvaluationRepository:
    """Persist bounded evaluation records in a versioned workspace database."""

    def __init__(self, workspace: Path, redactor: SecretRedactor) -> None:
        """Create the repository and recover regenerable corrupt storage."""
        self._workspace = workspace.resolve()
        self._root = self._workspace / ".harness" / "evaluations"
        self._path = self._root / "evaluations.sqlite3"
        self._redactor = redactor
        self._lock = threading.RLock()
        self._root.mkdir(parents=True, exist_ok=True)
        try:
            self._initialize()
        except sqlite3.DatabaseError:
            quarantine = self._root / (
                f"evaluations.corrupt-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.sqlite3"
            )
            if self._path.exists():
                self._path.replace(quarantine)
            self._initialize()

    def save_contract(self, contract: EvaluationContract) -> None:
        """Persist or replace one request contract."""
        self._save(
            "contract", contract.contract_id, contract, contract.session_id, contract.request_number
        )

    def get_contract(self, session_id: str, request_number: int) -> EvaluationContract | None:
        """Return one request contract when available."""
        value = self._find_session("contract", session_id, request_number)
        return _contract(value) if value is not None else None

    def save_observation(self, observation: EvaluationObservation) -> None:
        """Persist or replace one redacted observation."""
        self._save(
            "observation",
            observation.observation_id,
            observation,
            observation.session_id,
            observation.request_number,
        )

    def get_observation(self, session_id: str, request_number: int) -> EvaluationObservation | None:
        """Return one request observation when available."""
        value = self._find_session("observation", session_id, request_number)
        return _observation(value) if value is not None else None

    def list_observations(self, limit: int = 20) -> tuple[EvaluationObservation, ...]:
        """Return recent observations newest first."""
        return tuple(_observation(item) for item in self._list("observation", limit))

    def mark_observation(
        self, session_id: str, request_number: int, outcome: str, note: str
    ) -> EvaluationObservation:
        """Attach a bounded explicit user outcome to an observation."""
        if outcome not in {"pass", "fail"}:
            raise SessionError("Evaluation outcome must be pass or fail")
        current = self.get_observation(session_id, request_number)
        if current is None:
            raise SessionError("Evaluation observation was not found")
        updated = replace(
            current,
            user_mark=cast(Literal["pass", "fail"], outcome),
            user_note=self._redactor.redact(" ".join(note.split()))[:500],
        )
        self.save_observation(updated)
        return updated

    def save_handoff(self, snapshot: HandoffSnapshot) -> None:
        """Persist the latest request handoff snapshot."""
        record_id = f"{snapshot.session_id}:{snapshot.request_number}"
        self._save("handoff", record_id, snapshot, snapshot.session_id, snapshot.request_number)

    def latest_handoff(self, session_id: str) -> HandoffSnapshot | None:
        """Return the latest handoff for a session."""
        values = self._query(
            "SELECT payload FROM records WHERE kind='handoff' AND session_id=? "
            "ORDER BY request_number DESC LIMIT 1",
            (session_id,),
        )
        return _handoff(json.loads(values[0][0])) if values else None

    def save_run(self, run: EvaluationRun) -> None:
        """Persist an evaluation-suite run."""
        self._save("run", run.run_id, run)

    def get_run(self, run_id: str) -> EvaluationRun | None:
        """Return one evaluation run."""
        value = self._get("run", run_id)
        return _run(value) if value is not None else None

    def save_comparison(self, comparison: CandidateComparison) -> None:
        """Persist one immutable comparison report."""
        self._save("comparison", comparison.comparison_id, comparison)

    def save_candidate(self, candidate: HarnessCandidate) -> None:
        """Persist or update a controlled candidate proposal."""
        self._save("candidate", candidate.candidate_id, candidate)

    def get_candidate(self, candidate_id: str) -> HarnessCandidate | None:
        """Return one candidate proposal."""
        value = self._get("candidate", candidate_id)
        return _candidate(value) if value is not None else None

    def list_candidates(self, limit: int = 20) -> tuple[HarnessCandidate, ...]:
        """Return recent candidates newest first."""
        return tuple(_candidate(item) for item in self._list("candidate", limit))

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            version = connection.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            if version is not None and version[0] != "1":
                raise sqlite3.DatabaseError("Unsupported evaluation schema")
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version','1')"
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('workspace',?)",
                (str(self._workspace),),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
        except sqlite3.DatabaseError:
            connection.close()
            raise
        return connection

    def _save(
        self,
        kind: str,
        record_id: str,
        value: RecordValue,
        session_id: str = "",
        request_number: int = 0,
    ) -> None:
        raw = self._redactor.redact(json.dumps(asdict(value), ensure_ascii=False))
        if len(raw) > 100_000:
            raise SessionError("Evaluation record exceeds the storage limit")
        created_at = str(getattr(value, "created_at", "")) or datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO records(kind,record_id,session_id,request_number,"
                "created_at,payload) VALUES(?,?,?,?,?,?)",
                (kind, record_id, session_id, request_number, created_at, raw),
            )

    def _get(self, kind: str, record_id: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT payload FROM records WHERE kind=? AND record_id=?", (kind, record_id)
        )
        return json.loads(rows[0][0]) if rows else None

    def _find_session(
        self, kind: str, session_id: str, request_number: int
    ) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT payload FROM records WHERE kind=? AND session_id=? AND request_number=? "
            "ORDER BY created_at DESC LIMIT 1",
            (kind, session_id, request_number),
        )
        return json.loads(rows[0][0]) if rows else None

    def _list(self, kind: str, limit: int) -> tuple[dict[str, Any], ...]:
        if not 1 <= limit <= 1_000:
            raise SessionError("Evaluation list limit must be between 1 and 1000")
        rows = self._query(
            "SELECT payload FROM records WHERE kind=? ORDER BY created_at DESC LIMIT ?",
            (kind, limit),
        )
        return tuple(json.loads(row[0]) for row in rows)

    def _query(self, sql: str, values: tuple[object, ...]) -> list[tuple[Any, ...]]:
        with self._lock, self._connect() as connection:
            return list(connection.execute(sql, values).fetchall())


def _contract(value: dict[str, Any]) -> EvaluationContract:
    value["required_tool_groups"] = tuple(tuple(item) for item in value["required_tool_groups"])
    return EvaluationContract(**value)


def _score(value: dict[str, Any]) -> EvaluationScore:
    return EvaluationScore(**value)


def _observation(value: dict[str, Any]) -> EvaluationObservation:
    value["score"] = _score(value["score"])
    for name in ("completed", "failures", "evidence_sequences"):
        value[name] = tuple(value[name])
    return EvaluationObservation(**value)


def _handoff(value: dict[str, Any]) -> HandoffSnapshot:
    for name in ("completed", "remaining", "failures", "changed_files", "checks"):
        value[name] = tuple(value[name])
    return HandoffSnapshot(**value)


def _run(value: dict[str, Any]) -> EvaluationRun:
    value["case_ids"] = tuple(value["case_ids"])
    value["observation_ids"] = tuple(value["observation_ids"])
    return EvaluationRun(**value)


def _candidate(value: dict[str, Any]) -> HarnessCandidate:
    for name in ("component_ids", "predicted_changes", "evidence_ids", "risks"):
        value[name] = tuple(value[name])
    return HarnessCandidate(**value)


def read_git_revision(workspace: Path) -> str:
    """Return the current Git revision without invoking a shell or mutating Git state."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = result.stdout.strip()
    return value if result.returncode == 0 and len(value) == 40 else "unknown"
