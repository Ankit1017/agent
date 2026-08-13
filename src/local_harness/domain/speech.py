"""Provider-neutral speech synthesis values."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpeechFormat:
    """Describe one raw PCM speech stream."""

    sample_rate: int
    channels: int = 1
    sample_width: int = 2
    encoding: str = "s16le"


@dataclass(frozen=True, slots=True)
class SpeechVoice:
    """Describe one configured local speech voice."""

    voice_id: str
    display_name: str
    language: str
    audio_format: SpeechFormat
    license_summary: str
    default: bool = False
    loaded: bool = False


@dataclass(frozen=True, slots=True)
class SpeechRequest:
    """Represent one validated provider-neutral synthesis request."""

    text: str
    voice_id: str
    rate: float = 1.0


@dataclass(frozen=True, slots=True)
class SpeechStream:
    """Expose bounded synthesis metadata and pull-based PCM chunks."""

    voice: SpeechVoice
    chunks: Iterator[bytes]
    redacted: bool = False
