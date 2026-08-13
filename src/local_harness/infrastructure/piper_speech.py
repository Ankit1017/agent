"""In-process Piper adapter for bounded local streaming speech."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Protocol, cast

from piper import PiperVoice
from piper.config import SynthesisConfig

from local_harness.domain.errors import (
    ConfigurationError,
    SpeechBusyError,
    SpeechError,
    SpeechUnavailableError,
    SpeechValidationError,
)
from local_harness.domain.speech import SpeechFormat, SpeechRequest, SpeechVoice


class _PiperChunk(Protocol):
    """Describe the Piper chunk fields consumed by the adapter."""

    sample_rate: int
    sample_width: int
    sample_channels: int
    audio_int16_bytes: bytes


class _PiperVoice(Protocol):
    """Describe the loaded Piper voice behavior consumed by the adapter."""

    def synthesize(
        self, text: str, syn_config: SynthesisConfig | None = None
    ) -> Iterable[_PiperChunk]:
        """Yield raw audio chunks for text."""


VoiceLoader = Callable[[Path], _PiperVoice]

_VOICE_CATALOG: dict[str, tuple[str, str, str]] = {
    "en_US-lessac-medium": (
        "Lessac",
        "English (United States)",
        "Prototype voice; review the Lessac dataset terms before redistribution.",
    ),
    "hi_IN-priyamvada-medium": (
        "Priyamvada",
        "Hindi (India)",
        "Noncommercial prototype only; dataset license CC BY-NC-SA 4.0.",
    ),
    "hi_IN-rohan-medium": (
        "Rohan",
        "Hindi (India)",
        "Prototype only; separate IITM IndicTTS dataset terms apply.",
    ),
}
_PCM_FORMAT = SpeechFormat(sample_rate=22_050)


class _ReleasingPcmStream(Iterator[bytes]):
    """Release the global synthesis reservation on exhaustion or cancellation."""

    def __init__(self, source: Iterable[_PiperChunk], reservation: threading.Lock) -> None:
        self._source = iter(source)
        self._reservation = reservation
        self._closed = False

    def __iter__(self) -> _ReleasingPcmStream:
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        try:
            while True:
                chunk = next(self._source)
                if (
                    chunk.sample_rate != _PCM_FORMAT.sample_rate
                    or chunk.sample_width != _PCM_FORMAT.sample_width
                    or chunk.sample_channels != _PCM_FORMAT.channels
                ):
                    raise SpeechUnavailableError(
                        "The speech provider returned an unsupported audio format"
                    )
                if chunk.audio_int16_bytes:
                    return chunk.audio_int16_bytes
        except StopIteration:
            self.close()
            raise
        except SpeechError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise SpeechUnavailableError("Local speech synthesis failed") from exc

    def close(self) -> None:
        """Cancel the provider iterator and release the active slot exactly once."""
        if self._closed:
            return
        self._closed = True
        close = getattr(self._source, "close", None)
        if callable(close):
            close()
        self._reservation.release()


class PiperSpeechSynthesizer:
    """Keep allowlisted Piper voices warm and stream one request at a time."""

    def __init__(
        self,
        model_directory: Path,
        voice_ids: tuple[str, ...],
        default_voice: str,
        *,
        voice_loader: VoiceLoader | None = None,
    ) -> None:
        """Validate local voice artifacts and preload the configured default."""
        self._directory = model_directory
        self._voice_ids = voice_ids
        self._default_voice = default_voice
        self._voice_loader: VoiceLoader = voice_loader or (
            lambda path: cast(_PiperVoice, PiperVoice.load(path))
        )
        self._loaded: dict[str, _PiperVoice] = {}
        self._load_lock = threading.Lock()
        self._active = threading.Lock()
        unknown = [voice_id for voice_id in voice_ids if voice_id not in _VOICE_CATALOG]
        if unknown:
            raise ConfigurationError("HARNESS_TTS_VOICES contains an unsupported voice")
        self._load(default_voice, startup=True)

    def voices(self) -> tuple[SpeechVoice, ...]:
        """Return safe configured metadata without exposing model paths."""
        with self._load_lock:
            loaded = frozenset(self._loaded)
        return tuple(
            SpeechVoice(
                voice_id=voice_id,
                display_name=_VOICE_CATALOG[voice_id][0],
                language=_VOICE_CATALOG[voice_id][1],
                audio_format=_PCM_FORMAT,
                license_summary=_VOICE_CATALOG[voice_id][2],
                default=voice_id == self._default_voice,
                loaded=voice_id in loaded,
            )
            for voice_id in self._voice_ids
        )

    def synthesize(self, request: SpeechRequest) -> _ReleasingPcmStream:
        """Reserve Piper immediately and return a releasing PCM iterator."""
        if request.voice_id not in self._voice_ids:
            raise SpeechValidationError("Voice is not configured")
        if not self._active.acquire(blocking=False):
            raise SpeechBusyError("The local speech engine is already speaking")
        try:
            voice = self._load(request.voice_id, startup=False)
            source = voice.synthesize(
                request.text,
                syn_config=SynthesisConfig(length_scale=1.0 / request.rate),
            )
        except Exception as exc:
            self._active.release()
            if isinstance(exc, SpeechError):
                raise
            raise SpeechUnavailableError("The local speech voice could not start") from exc
        return _ReleasingPcmStream(source, self._active)

    def _load(self, voice_id: str, *, startup: bool) -> _PiperVoice:
        with self._load_lock:
            existing = self._loaded.get(voice_id)
            if existing is not None:
                return existing
            if (
                not self._model_path(voice_id).is_file()
                or not self._config_path(voice_id).is_file()
            ):
                if startup:
                    raise ConfigurationError(
                        "The default Piper voice is missing. Run scripts/setup-voices.ps1."
                    )
                raise SpeechUnavailableError(
                    "The selected Piper voice is not installed. Run the voice setup first."
                )
            try:
                loaded = self._voice_loader(self._model_path(voice_id))
            except Exception as exc:
                if startup:
                    raise ConfigurationError("The default Piper voice could not be loaded") from exc
                raise SpeechUnavailableError(
                    "The selected Piper voice could not be loaded"
                ) from exc
            self._loaded[voice_id] = loaded
            return loaded

    def _model_path(self, voice_id: str) -> Path:
        return self._directory / f"{voice_id}.onnx"

    def _config_path(self, voice_id: str) -> Path:
        return self._directory / f"{voice_id}.onnx.json"
