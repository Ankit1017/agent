"""Bounded provider-neutral speech synthesis use case."""

from __future__ import annotations

from collections.abc import Callable

from local_harness.application.ports import SpeechSynthesizer
from local_harness.domain.errors import SpeechValidationError
from local_harness.domain.speech import SpeechRequest, SpeechStream, SpeechVoice


class SpeechService:
    """Validate, sanitize, and route local speech requests."""

    def __init__(
        self,
        synthesizer: SpeechSynthesizer,
        sanitizer: Callable[[str], tuple[str, bool]],
        *,
        default_voice: str,
        max_chars: int,
    ) -> None:
        """Create a speech service over one configured synthesizer."""
        self._synthesizer = synthesizer
        self._sanitizer = sanitizer
        self._default_voice = default_voice
        self._max_chars = max_chars

    def voices(self) -> tuple[SpeechVoice, ...]:
        """Return configured voices with the service default marked."""
        return tuple(
            SpeechVoice(
                voice_id=voice.voice_id,
                display_name=voice.display_name,
                language=voice.language,
                audio_format=voice.audio_format,
                license_summary=voice.license_summary,
                default=voice.voice_id == self._default_voice,
                loaded=voice.loaded,
            )
            for voice in self._synthesizer.voices()
        )

    def synthesize(self, text: str, voice_id: str, rate: float = 1.0) -> SpeechStream:
        """Validate and sanitize text before reserving the speech provider."""
        normalized = text.strip()
        if not normalized:
            raise SpeechValidationError("Speech text cannot be empty")
        if len(normalized) > self._max_chars:
            raise SpeechValidationError(
                f"Speech text exceeds the {self._max_chars}-character limit"
            )
        voices = {voice.voice_id: voice for voice in self.voices()}
        if voice_id not in voices:
            raise SpeechValidationError("Voice is not configured")
        if not 0.75 <= rate <= 1.50:
            raise SpeechValidationError("Speech rate must be between 0.75 and 1.50")
        safe_text, redacted = self._sanitizer(normalized)
        request = SpeechRequest(safe_text, voice_id, rate)
        chunks = self._synthesizer.synthesize(request)
        return SpeechStream(voices[voice_id], chunks, redacted)
