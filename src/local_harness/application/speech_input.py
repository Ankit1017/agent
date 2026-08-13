"""Bounded local wake-word and microphone transcription workflow."""

from __future__ import annotations

import re
import sys
import threading
import time
from array import array
from collections import deque
from collections.abc import Callable

from local_harness.application.ports import SpeechRecognizer, WakeWordDetector, WakeWordStream
from local_harness.domain.errors import (
    SpeechInputBusyError,
    SpeechInputUnavailableError,
    SpeechInputValidationError,
)
from local_harness.domain.speech_input import (
    PcmInputFormat,
    SpeechInputCompletion,
    SpeechInputEvent,
    SpeechInputMode,
    SpeechInputTranscript,
)
from local_harness.identifiers import new_session_id

_FRAME_MAX_BYTES = 32_000
_BYTES_PER_SECOND = 32_000
_PRE_ROLL_BYTES = 64_000
_SPEECH_RMS_THRESHOLD = 350
_COMMAND_START_TIMEOUT_SECONDS = 5
_MAX_TRANSCRIPT_CHARS = 5_000


class SpeechInputService:
    """Reserve and create one protected local microphone session at a time."""

    def __init__(
        self,
        wake_detector: WakeWordDetector,
        recognizer: SpeechRecognizer,
        sanitizer: Callable[[str], tuple[str, bool]],
        *,
        wake_phrase: str,
        languages: tuple[str, ...],
        max_seconds: int,
        silence_ms: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind local providers and deterministic stream limits."""
        self._wake_detector = wake_detector
        self._recognizer = recognizer
        self._sanitizer = sanitizer
        self._wake_phrase = wake_phrase
        self._languages = languages
        self._max_seconds = max_seconds
        self._silence_ms = silence_ms
        self._clock = clock
        self._active = threading.Lock()

    @property
    def audio_format(self) -> PcmInputFormat:
        """Return the exact browser microphone format accepted by the service."""
        return PcmInputFormat()

    @property
    def wake_phrase(self) -> str:
        """Return the configured display wake phrase."""
        return self._wake_phrase

    @property
    def languages(self) -> tuple[str, ...]:
        """Return the exact locally supported language codes."""
        return self._languages

    @property
    def max_seconds(self) -> int:
        """Return the bounded maximum utterance duration."""
        return self._max_seconds

    @property
    def silence_ms(self) -> int:
        """Return the end-of-speech silence duration."""
        return self._silence_ms

    def open_session(self, mode: SpeechInputMode) -> SpeechInputSession:
        """Fail quickly when busy and create a releasing input session."""
        if not self._active.acquire(blocking=False):
            raise SpeechInputBusyError("Another microphone session is already active")
        try:
            stream = self._wake_detector.open_stream()
            return SpeechInputSession(
                stream,
                self._recognizer,
                self._sanitizer,
                self._active,
                mode=mode,
                wake_phrase=self._wake_phrase,
                languages=self._languages,
                max_seconds=self._max_seconds,
                silence_ms=self._silence_ms,
                clock=self._clock,
            )
        except Exception:
            self._active.release()
            raise


class SpeechInputSession:
    """Apply wake, VAD, rate, duration, cancellation, and transcript boundaries."""

    def __init__(
        self,
        wake_stream: WakeWordStream,
        recognizer: SpeechRecognizer,
        sanitizer: Callable[[str], tuple[str, bool]],
        reservation: threading.Lock,
        *,
        mode: SpeechInputMode,
        wake_phrase: str,
        languages: tuple[str, ...],
        max_seconds: int,
        silence_ms: int,
        clock: Callable[[], float],
    ) -> None:
        """Create one stateful session over preloaded providers."""
        self._wake_stream = wake_stream
        self._recognizer = recognizer
        self._sanitizer = sanitizer
        self._reservation = reservation
        self._mode = mode
        self._wake_phrase = wake_phrase
        self._languages = languages
        self._max_bytes = max_seconds * _BYTES_PER_SECOND
        self._silence_bytes = silence_ms * _BYTES_PER_SECOND // 1_000
        self._clock = clock
        self._opened_at = clock()
        self._received_bytes = 0
        self._ring: deque[bytes] = deque()
        self._ring_bytes = 0
        self._utterance = bytearray()
        self._silence_bytes_seen = 0
        self._speech_started = False
        self._command_deadline: float | None = None
        self._pending: tuple[bytes, SpeechInputCompletion] | None = None
        self._state = "wake" if mode == "wake" else "listening"
        self._closed = False

    @property
    def mode(self) -> SpeechInputMode:
        """Return the currently selected wake or tap behavior."""
        return self._mode

    def accept(self, chunk: bytes) -> tuple[SpeechInputEvent, ...]:
        """Consume one bounded PCM frame and return observable state events."""
        self._ensure_open()
        self._validate_chunk(chunk)
        if self._state in {"paused", "transcribing"}:
            return ()
        if self._state == "wake":
            self._append_ring(chunk)
            if not self._wake_stream.accept(chunk):
                return ()
            self._utterance = bytearray(b"".join(self._ring))
            self._speech_started = _pcm_rms(chunk) >= _SPEECH_RMS_THRESHOLD
            self._silence_bytes_seen = 0
            self._command_deadline = self._clock() + _COMMAND_START_TIMEOUT_SECONDS
            self._state = "listening"
            return (SpeechInputEvent("wake_detected"),)
        return self._accept_utterance(chunk)

    def begin_tap(self) -> tuple[SpeechInputEvent, ...]:
        """Start a fresh utterance immediately without wake detection."""
        self._ensure_open()
        self._mode = "tap"
        self._reset_utterance()
        self._state = "listening"
        self._command_deadline = self._clock() + _COMMAND_START_TIMEOUT_SECONDS
        return (SpeechInputEvent("ready", reason="tap"),)

    def finish(self) -> tuple[SpeechInputEvent, ...]:
        """Finish a spoken utterance early or report that no speech was heard."""
        self._ensure_open()
        if self._state != "listening" or not self._speech_started:
            self.rearm()
            return (SpeechInputEvent("timeout", reason="no_speech"),)
        return self._prepare_transcription("manual_stop")

    def transcribe_pending(self) -> SpeechInputEvent:
        """Transcribe and sanitize the prepared utterance exactly once."""
        self._ensure_open()
        if self._pending is None:
            raise SpeechInputValidationError("No microphone utterance is ready")
        pcm, completion = self._pending
        self._pending = None
        try:
            result = self._recognizer.transcribe(pcm, self._languages)
        except SpeechInputUnavailableError:
            self._state = "paused"
            raise
        except Exception as exc:
            self._state = "paused"
            raise SpeechInputUnavailableError("Local speech transcription failed") from exc
        normalized = result.text.strip()
        normalized = _strip_leading_wake_phrase(normalized, self._wake_phrase)
        safe_text, redacted = self._sanitizer(normalized)
        safe_text = safe_text.strip()
        if not safe_text:
            if self._mode == "wake":
                self._reset_utterance()
                self._state = "listening"
                self._command_deadline = self._clock() + _COMMAND_START_TIMEOUT_SECONDS
                return SpeechInputEvent("ready", reason="followup")
            self._state = "paused"
            return SpeechInputEvent("timeout", reason="empty_transcript")
        if len(safe_text) > _MAX_TRANSCRIPT_CHARS:
            self._state = "paused"
            raise SpeechInputValidationError("Recognized speech exceeds the message limit")
        if result.language not in self._languages:
            self._state = "paused"
            raise SpeechInputValidationError("Recognized speech language is not configured")
        self._state = "paused"
        return SpeechInputEvent(
            "transcript",
            SpeechInputTranscript(
                new_session_id(), safe_text, result.language, redacted, completion
            ),
        )

    def pause(self) -> tuple[SpeechInputEvent, ...]:
        """Discard transient audio and stop decoding until explicitly re-armed."""
        self._ensure_open()
        self._reset_utterance()
        self._state = "paused"
        return (SpeechInputEvent("paused"),)

    def rearm(self) -> tuple[SpeechInputEvent, ...]:
        """Return to wake mode after generation or assistant playback."""
        self._ensure_open()
        self._mode = "wake"
        self._reset_utterance()
        self._ring.clear()
        self._ring_bytes = 0
        self._wake_stream.reset()
        self._state = "wake"
        return (SpeechInputEvent("ready", reason="wake"),)

    def cancel(self) -> tuple[SpeechInputEvent, ...]:
        """Cancel current capture while retaining the authenticated session."""
        self._ensure_open()
        self._reset_utterance()
        self._state = "paused"
        return (SpeechInputEvent("cancelled"),)

    def close(self) -> None:
        """Release provider and global reservation resources exactly once."""
        if self._closed:
            return
        self._closed = True
        try:
            self._wake_stream.close()
        finally:
            self._reservation.release()

    def _accept_utterance(self, chunk: bytes) -> tuple[SpeechInputEvent, ...]:
        self._utterance.extend(chunk)
        events: list[SpeechInputEvent] = []
        speaking = _pcm_rms(chunk) >= _SPEECH_RMS_THRESHOLD
        if speaking:
            if not self._speech_started:
                self._speech_started = True
                events.append(SpeechInputEvent("speech_started"))
            self._silence_bytes_seen = 0
        elif self._speech_started:
            self._silence_bytes_seen += len(chunk)
        elif self._command_deadline is not None and self._clock() >= self._command_deadline:
            self.rearm()
            return (SpeechInputEvent("timeout", reason="no_speech"),)
        if len(self._utterance) >= self._max_bytes:
            events.extend(self._prepare_transcription("max_duration"))
        elif self._speech_started and self._silence_bytes_seen >= self._silence_bytes:
            events.extend(self._prepare_transcription("silence"))
        return tuple(events)

    def _prepare_transcription(
        self, completion: SpeechInputCompletion
    ) -> tuple[SpeechInputEvent, ...]:
        self._pending = (bytes(self._utterance[: self._max_bytes]), completion)
        self._state = "transcribing"
        self._utterance.clear()
        return (SpeechInputEvent("transcribing", reason=completion),)

    def _append_ring(self, chunk: bytes) -> None:
        self._ring.append(chunk)
        self._ring_bytes += len(chunk)
        while self._ring and self._ring_bytes > _PRE_ROLL_BYTES:
            self._ring_bytes -= len(self._ring.popleft())

    def _reset_utterance(self) -> None:
        self._utterance.clear()
        self._pending = None
        self._silence_bytes_seen = 0
        self._speech_started = False
        self._command_deadline = None

    def _validate_chunk(self, chunk: bytes) -> None:
        if not chunk or len(chunk) > _FRAME_MAX_BYTES or len(chunk) % 2:
            raise SpeechInputValidationError("Invalid microphone PCM frame")
        self._received_bytes += len(chunk)
        elapsed = max(0.0, self._clock() - self._opened_at)
        if self._received_bytes > _BYTES_PER_SECOND * 2 + int(elapsed * _BYTES_PER_SECOND * 2):
            raise SpeechInputValidationError("Microphone PCM byte rate exceeded")

    def _ensure_open(self) -> None:
        if self._closed:
            raise SpeechInputValidationError("Microphone session is closed")


def _pcm_rms(chunk: bytes) -> int:
    samples = array("h")
    samples.frombytes(chunk)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0
    return int((sum(sample * sample for sample in samples) / len(samples)) ** 0.5)


def _strip_leading_wake_phrase(text: str, wake_phrase: str) -> str:
    words = [re.escape(word) for word in wake_phrase.split()]
    pattern = r"^\s*" + r"[\s,.:;!?_-]+".join(words) + r"[\s,.:;!?_-]*"
    return re.sub(pattern, "", text, count=1, flags=re.IGNORECASE).strip()
