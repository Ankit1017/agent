"""Bounded text-to-speech and Audio2Face orchestration."""

from __future__ import annotations

import sys
import threading
from array import array
from dataclasses import replace

from local_harness.application.ports import FaceAnimator, FaceAvatarRepository
from local_harness.application.speech import SpeechService
from local_harness.domain.audio2face import (
    AnimatedSpeech,
    Audio2FaceStatus,
    FaceAnimation,
    FaceAvatarAsset,
    FaceRigAnimation,
)
from local_harness.domain.errors import Audio2FaceBusyError, Audio2FaceValidationError

_CANONICAL_RATE = 16_000


class AnimatedSpeechService:
    """Generate one bounded local utterance and matching facial animation."""

    def __init__(
        self,
        speech: SpeechService,
        animator: FaceAnimator,
        avatar_repository: FaceAvatarRepository | None = None,
        *,
        max_seconds: int = 60,
    ) -> None:
        """Create the one-at-a-time animation pipeline."""
        if not 1 <= max_seconds <= 60:
            raise ValueError("Audio2Face maximum duration must be between 1 and 60 seconds")
        self._speech = speech
        self._animator = animator
        self._avatar_repository = avatar_repository
        self._max_seconds = max_seconds
        self._active = threading.Lock()

    def status(self) -> Audio2FaceStatus:
        """Return safe provider setup status."""
        status = self._animator.status()
        if self._avatar_repository is None:
            return status
        avatar = self._avatar_repository.status()
        setup = status.setup if not status.available else avatar.setup
        return replace(
            status,
            avatar_available=avatar.available,
            avatar_name=avatar.name,
            face_control_count=len(avatar.face_controls),
            tongue_control_count=len(avatar.tongue_controls),
            default_avatar_id=self._avatar_repository.default_id(),
            avatars=self._avatar_repository.catalog(),
            setup=setup,
        )

    def avatar_asset(self, avatar_id: str | None = None) -> FaceAvatarAsset:
        """Return one exact setup-validated 3D avatar."""
        if self._avatar_repository is None:
            raise Audio2FaceValidationError("The local 3D avatar is not configured")
        return self._avatar_repository.asset(avatar_id)

    def generate(
        self,
        text: str,
        voice_id: str,
        rate: float = 1.0,
        avatar_id: str | None = None,
    ) -> AnimatedSpeech:
        """Synthesize, bound, resample, and animate one sanitized utterance."""
        if not self._active.acquire(blocking=False):
            raise Audio2FaceBusyError("Another Audio2Face generation is active")
        chunks = None
        try:
            speech = self._speech.synthesize(text, voice_id, rate)
            audio_format = speech.voice.audio_format
            if (
                audio_format.channels != 1
                or audio_format.sample_width != 2
                or audio_format.encoding != "s16le"
            ):
                raise Audio2FaceValidationError("Audio2Face requires mono signed 16-bit PCM")
            chunks = speech.chunks
            maximum = self._max_seconds * audio_format.sample_rate * audio_format.sample_width
            collected = bytearray()
            for chunk in chunks:
                collected.extend(chunk)
                if len(collected) > maximum:
                    raise Audio2FaceValidationError(
                        f"Animated speech exceeds the {self._max_seconds}-second limit"
                    )
            if not collected:
                raise Audio2FaceValidationError("Speech synthesis returned no audio")
            canonical = _resample_s16le_mono(
                bytes(collected), audio_format.sample_rate, _CANONICAL_RATE
            )
            animation = self._filter_for_avatar(self._animator.animate(canonical), avatar_id)
            return AnimatedSpeech(
                audio=bytes(collected),
                audio_format=audio_format,
                animation=animation,
                voice_id=speech.voice.voice_id,
                redacted=speech.redacted,
            )
        finally:
            close = getattr(chunks, "close", None)
            if callable(close):
                close()
            self._active.release()

    def _filter_for_avatar(
        self, animation: FaceAnimation, avatar_id: str | None = None
    ) -> FaceAnimation:
        """Retain mandatory face controls and only avatar-supported tongue controls."""
        rig = animation.rig
        if rig is None:
            return animation
        supported_tongues: set[str] = set()
        if self._avatar_repository is not None:
            supported_tongues.update(self._avatar_repository.status(avatar_id).tongue_controls)
        keep_tongues = tuple(name for name in rig.tongue_controls if name in supported_tongues)
        if keep_tongues == rig.tongue_controls:
            return animation
        selected = tuple(range(len(rig.face_controls))) + tuple(
            len(rig.face_controls) + rig.tongue_controls.index(name) for name in keep_tongues
        )
        source = array("f")
        source.frombytes(rig.weights)
        if sys.byteorder != "little":
            source.byteswap()
        source_width = len(rig.face_controls) + len(rig.tongue_controls)
        output = array("f")
        for frame in range(rig.frame_count):
            offset = frame * source_width
            output.extend(source[offset + index] for index in selected)
        if sys.byteorder != "little":
            output.byteswap()
        filtered = FaceRigAnimation(
            rig.encoding,
            rig.fps,
            rig.frame_count,
            rig.face_controls,
            keep_tongues,
            output.tobytes(),
        )
        return replace(animation, rig=filtered)


def _resample_s16le_mono(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    """Linearly resample bounded signed 16-bit mono PCM without external processes."""
    if source_rate <= 0 or target_rate <= 0 or len(pcm) % 2:
        raise Audio2FaceValidationError("Speech PCM metadata is invalid")
    if source_rate == target_rate:
        return pcm
    source = array("h")
    source.frombytes(pcm)
    if sys.byteorder != "little":
        source.byteswap()
    if not source:
        return b""
    output_count = max(1, round(len(source) * target_rate / source_rate))
    output = array("h")
    for index in range(output_count):
        position = index * source_rate / target_rate
        lower = min(int(position), len(source) - 1)
        upper = min(lower + 1, len(source) - 1)
        fraction = position - lower
        value = round(source[lower] + (source[upper] - source[lower]) * fraction)
        output.append(max(-32_768, min(32_767, value)))
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()
