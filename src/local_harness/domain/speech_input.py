"""Provider-neutral values for bounded local microphone recognition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SpeechInputMode = Literal["wake", "tap"]
SpeechInputEventType = Literal[
    "ready",
    "wake_detected",
    "speech_started",
    "transcribing",
    "transcript",
    "timeout",
    "paused",
    "cancelled",
]
SpeechInputCompletion = Literal["silence", "max_duration", "manual_stop"]


@dataclass(frozen=True, slots=True)
class PcmInputFormat:
    """Describe the only accepted browser microphone stream format."""

    sample_rate: int = 16_000
    channels: int = 1
    sample_width: int = 2
    encoding: str = "s16le"


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    """Hold raw provider-neutral recognition output before sanitization."""

    text: str
    language: str


@dataclass(frozen=True, slots=True)
class SpeechInputTranscript:
    """Expose one sanitized bounded transcript without retaining its audio."""

    utterance_id: str
    text: str
    language: str
    redacted: bool
    completion: SpeechInputCompletion


@dataclass(frozen=True, slots=True)
class SpeechInputEvent:
    """Describe one observable microphone-session state transition."""

    type: SpeechInputEventType
    transcript: SpeechInputTranscript | None = None
    reason: str = ""
