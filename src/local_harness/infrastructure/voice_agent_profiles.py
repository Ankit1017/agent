"""Atomic control-workspace JSON persistence for voice-agent profiles."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from local_harness.domain.errors import (
    VoiceAgentProfileNotFoundError,
    VoiceAgentProfileStorageError,
    VoiceAgentProfileValidationError,
)
from local_harness.domain.voice_agent import VoiceAgentProfile
from local_harness.guardrails.redaction import SecretRedactor

_ID = re.compile(r"^[a-f0-9]{32}$")


class JsonVoiceAgentProfileRepository:
    """Store bounded profile documents under protected harness state."""

    def __init__(self, workspace: Path, redactor: SecretRedactor) -> None:
        """Bind the repository to a control workspace and redactor."""
        self._directory = workspace / ".harness" / "voice-agent-profiles"
        self._redactor = redactor
        self._lock = threading.RLock()

    def save(self, profile: VoiceAgentProfile) -> None:
        """Redact and atomically replace one profile document."""
        self._validate_id(profile.profile_id)
        with self._lock:
            try:
                self._directory.mkdir(parents=True, exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(
                    dir=self._directory, prefix=f".{profile.profile_id}.", suffix=".tmp"
                )
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                        json.dump(self._to_dict(profile), handle, ensure_ascii=False, indent=2)
                        handle.write("\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, self._path(profile.profile_id))
                finally:
                    Path(temporary).unlink(missing_ok=True)
            except (OSError, TypeError, ValueError) as exc:
                raise VoiceAgentProfileStorageError("Could not save voice-agent profile") from exc

    def load(self, profile_id: str) -> VoiceAgentProfile:
        """Load one supported profile document."""
        self._validate_id(profile_id)
        with self._lock:
            try:
                return self._from_dict(json.loads(self._path(profile_id).read_text("utf-8")))
            except FileNotFoundError as exc:
                raise VoiceAgentProfileNotFoundError("Voice-agent profile was not found") from exc
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise VoiceAgentProfileStorageError("Voice-agent profile is corrupt") from exc

    def list_profiles(self) -> list[VoiceAgentProfile]:
        """Return valid profiles newest first."""
        if not self._directory.exists():
            return []
        values: list[VoiceAgentProfile] = []
        for path in self._directory.glob("*.json"):
            try:
                values.append(self.load(path.stem))
            except (VoiceAgentProfileStorageError, VoiceAgentProfileValidationError):
                continue
        return sorted(values, key=lambda item: item.updated_at, reverse=True)

    def delete(self, profile_id: str) -> None:
        """Delete one exact profile document."""
        self._validate_id(profile_id)
        try:
            self._path(profile_id).unlink()
        except FileNotFoundError as exc:
            raise VoiceAgentProfileNotFoundError("Voice-agent profile was not found") from exc
        except OSError as exc:
            raise VoiceAgentProfileStorageError("Could not delete voice-agent profile") from exc

    def _to_dict(self, profile: VoiceAgentProfile) -> dict[str, object]:
        value = {name: getattr(profile, name) for name in profile.__dataclass_fields__}
        value["instructions"] = self._redactor.redact(profile.instructions)
        value["name"] = self._redactor.redact(profile.name)
        value["allowed_tools"] = list(profile.allowed_tools)
        return value

    @staticmethod
    def _from_dict(value: Any) -> VoiceAgentProfile:
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("Unsupported voice-agent profile schema")
        value = dict(value)
        tools = value.get("allowed_tools")
        if not isinstance(tools, list) or not all(isinstance(item, str) for item in tools):
            raise ValueError("Invalid profile tools")
        value["allowed_tools"] = tuple(tools)
        profile = VoiceAgentProfile(**value)
        JsonVoiceAgentProfileRepository._validate_id(profile.profile_id)
        return profile

    def _path(self, profile_id: str) -> Path:
        return self._directory / f"{profile_id}.json"

    @staticmethod
    def _validate_id(profile_id: str) -> None:
        if not _ID.fullmatch(profile_id):
            raise VoiceAgentProfileValidationError("Invalid voice-agent profile identifier")
