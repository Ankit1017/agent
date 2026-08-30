"""Provider-neutral values for audio-driven facial animation."""

from __future__ import annotations

from dataclasses import dataclass

from local_harness.domain.speech import SpeechFormat

ARKIT_FACE_CONTROLS: tuple[str, ...] = (
    "eyeBlinkLeft",
    "eyeLookDownLeft",
    "eyeLookInLeft",
    "eyeLookOutLeft",
    "eyeLookUpLeft",
    "eyeSquintLeft",
    "eyeWideLeft",
    "eyeBlinkRight",
    "eyeLookDownRight",
    "eyeLookInRight",
    "eyeLookOutRight",
    "eyeLookUpRight",
    "eyeSquintRight",
    "eyeWideRight",
    "jawForward",
    "jawLeft",
    "jawRight",
    "jawOpen",
    "mouthClose",
    "mouthFunnel",
    "mouthPucker",
    "mouthLeft",
    "mouthRight",
    "mouthSmileLeft",
    "mouthSmileRight",
    "mouthFrownLeft",
    "mouthFrownRight",
    "mouthDimpleLeft",
    "mouthDimpleRight",
    "mouthStretchLeft",
    "mouthStretchRight",
    "mouthRollLower",
    "mouthRollUpper",
    "mouthShrugLower",
    "mouthShrugUpper",
    "mouthPressLeft",
    "mouthPressRight",
    "mouthLowerDownLeft",
    "mouthLowerDownRight",
    "mouthUpperUpLeft",
    "mouthUpperUpRight",
    "browDownLeft",
    "browDownRight",
    "browInnerUp",
    "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff",
    "cheekSquintLeft",
    "cheekSquintRight",
    "noseSneerLeft",
    "noseSneerRight",
    "tongueOut",
)

AUDIO2FACE_TONGUE_CONTROLS: tuple[str, ...] = (
    "tongueTipUp",
    "tongueTipDown",
    "tongueTipLeft",
    "tongueTipRight",
    "tongueRollUp",
    "tongueRollDown",
    "tongueRollLeft",
    "tongueRollRight",
    "tongueUp",
    "tongueDown",
    "tongueLeft",
    "tongueRight",
    "tongueIn",
    "tongueStretch",
    "tongueWide",
    "tongueNarrow",
)


@dataclass(frozen=True, slots=True)
class FaceAnimationFrame:
    """Describe one bounded facial animation sample."""

    time_seconds: float
    mouth_open: float
    eye_x: float = 0.0
    eye_y: float = 0.0


@dataclass(frozen=True, slots=True)
class FaceRigAnimation:
    """Hold bounded frame-major facial controls for a renderer."""

    encoding: str
    fps: int
    frame_count: int
    face_controls: tuple[str, ...]
    tongue_controls: tuple[str, ...]
    weights: bytes


@dataclass(frozen=True, slots=True)
class FaceAnimation:
    """Describe one complete animation generated from in-memory PCM."""

    fps: int
    duration_seconds: float
    frames: tuple[FaceAnimationFrame, ...]
    model: str
    rig: FaceRigAnimation | None = None


@dataclass(frozen=True, slots=True)
class FaceAvatarAsset:
    """Describe one validated, fixed, browser-renderable avatar asset."""

    name: str
    sha256: str
    face_controls: tuple[str, ...]
    tongue_controls: tuple[str, ...]
    content: bytes
    avatar_id: str = "default"


@dataclass(frozen=True, slots=True)
class FaceAvatarStatus:
    """Report safe setup state for the fixed local avatar."""

    available: bool
    name: str
    face_controls: tuple[str, ...]
    tongue_controls: tuple[str, ...]
    setup: str
    avatar_id: str = "default"


@dataclass(frozen=True, slots=True)
class FaceAvatarChoice:
    """Describe one safe selectable local avatar without exposing its path."""

    avatar_id: str
    name: str
    face_control_count: int
    tongue_control_count: int
    sha256: str = ""


@dataclass(frozen=True, slots=True)
class AnimatedSpeech:
    """Pair local speech audio with its synchronized facial animation."""

    audio: bytes
    audio_format: SpeechFormat
    animation: FaceAnimation
    voice_id: str
    redacted: bool = False


@dataclass(frozen=True, slots=True)
class Audio2FaceStatus:
    """Report safe setup and runtime availability without local paths."""

    enabled: bool
    available: bool
    gpu_available: bool
    bridge_available: bool
    model_available: bool
    setup: str
    model: str
    max_seconds: int
    avatar_available: bool = False
    avatar_name: str = ""
    face_control_count: int = 0
    tongue_control_count: int = 0
    default_avatar_id: str = ""
    avatars: tuple[FaceAvatarChoice, ...] = ()
