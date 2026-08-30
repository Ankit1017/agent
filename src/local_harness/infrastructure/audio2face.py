"""Fixed-process adapter for NVIDIA Audio2Face facial animation."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import wave
from array import array
from pathlib import Path
from typing import cast

from local_harness.domain.audio2face import (
    ARKIT_FACE_CONTROLS,
    AUDIO2FACE_TONGUE_CONTROLS,
    Audio2FaceStatus,
    FaceAnimation,
    FaceAnimationFrame,
    FaceRigAnimation,
)
from local_harness.domain.errors import Audio2FaceUnavailableError

_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_RIG_BYTES = (60 * 60 + 2) * (52 + 16) * 4


class NvidiaAudio2FaceAnimator:
    """Invoke one startup-configured Audio2Face bridge with fixed arguments."""

    def __init__(
        self,
        bridge: Path,
        model: Path,
        runtime_root: Path,
        *,
        model_name: str = "mark",
        fps: int = 60,
        max_seconds: int = 60,
        timeout_seconds: int = 120,
        dependency_directories: tuple[Path, ...] = (),
        enabled: bool = True,
    ) -> None:
        """Create an adapter over protected, server-selected artifacts."""
        self._bridge = bridge.resolve()
        self._model = model.resolve()
        self._runtime_root = runtime_root.resolve()
        self._model_name = model_name
        self._fps = fps
        self._max_seconds = max_seconds
        self._timeout_seconds = timeout_seconds
        self._dependency_directories = tuple(item.resolve() for item in dependency_directories)
        self._enabled = enabled
        self._runtime_root.mkdir(parents=True, exist_ok=True)

    def status(self) -> Audio2FaceStatus:
        """Return safe availability without revealing configured filesystem paths."""
        bridge = self._bridge.is_file()
        model = self._model.is_file()
        gpu = _nvidia_gpu_available()
        available = self._enabled and bridge and model and gpu
        if available:
            setup = "Audio2Face is ready for local facial animation."
        elif not self._enabled:
            setup = "Set HARNESS_AUDIO2FACE_ENABLED=true after running setup-audio2face.ps1."
        else:
            missing = []
            if not gpu:
                missing.append("a compatible NVIDIA GPU/driver")
            if not bridge:
                missing.append("the Audio2Face bridge")
            if not model:
                missing.append("the generated Audio2Face model")
            setup = "Run scripts/setup-audio2face.ps1; missing " + ", ".join(missing) + "."
        return Audio2FaceStatus(
            enabled=self._enabled,
            available=available,
            gpu_available=gpu,
            bridge_available=bridge,
            model_available=model,
            setup=setup,
            model=self._model_name,
            max_seconds=self._max_seconds,
        )

    def animate(self, pcm_s16le_16khz: bytes) -> FaceAnimation:
        """Generate and validate animation frames without retaining input audio."""
        status = self.status()
        if not status.available:
            raise Audio2FaceUnavailableError(status.setup)
        maximum = self._max_seconds * 16_000 * 2
        if not pcm_s16le_16khz or len(pcm_s16le_16khz) > maximum:
            raise Audio2FaceUnavailableError(
                "Audio2Face PCM is empty or exceeds its duration limit"
            )
        with tempfile.TemporaryDirectory(prefix="request-", dir=self._runtime_root) as temporary:
            root = Path(temporary).resolve()
            input_path = root / "speech.wav"
            output_path = root / "animation.json"
            binary_path = root / "animation.bin"
            _write_wav(input_path, pcm_s16le_16khz)
            command = [
                str(self._bridge),
                "--input",
                str(input_path),
                "--model",
                str(self._model),
                "--output",
                str(output_path),
                "--fps",
                str(self._fps),
            ]
            environment = _fixed_environment(self._dependency_directories)
            creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                result = subprocess.run(
                    command,
                    cwd=root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=self._timeout_seconds,
                    check=False,
                    creationflags=creation_flags,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise Audio2FaceUnavailableError(
                    "The local Audio2Face process could not complete"
                ) from exc
            if result.returncode != 0 or not output_path.is_file():
                raise Audio2FaceUnavailableError("The local Audio2Face process failed")
            if output_path.stat().st_size > _MAX_OUTPUT_BYTES:
                raise Audio2FaceUnavailableError("Audio2Face returned excessive animation data")
            if binary_path.is_file() and binary_path.stat().st_size > _MAX_RIG_BYTES:
                raise Audio2FaceUnavailableError("Audio2Face returned excessive rig data")
            try:
                payload = cast(object, json.loads(output_path.read_text(encoding="utf-8")))
                binary = binary_path.read_bytes() if binary_path.is_file() else b""
                return _parse_animation(
                    payload,
                    binary,
                    self._fps,
                    self._max_seconds,
                    self._model_name,
                )
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise Audio2FaceUnavailableError(
                    "Audio2Face returned invalid animation data"
                ) from exc


def _parse_animation(
    value: object,
    binary: bytes,
    expected_fps: int,
    max_seconds: int,
    model_name: str,
) -> FaceAnimation:
    """Validate the closed bridge response and normalize bounded frame values."""
    if not isinstance(value, dict):
        raise ValueError("invalid animation envelope")
    version = value.get("version")
    expected_keys = (
        {"version", "fps", "duration", "frames"}
        if version == 1
        else {
            "version",
            "fps",
            "duration",
            "frame_count",
            "face_controls",
            "tongue_controls",
            "frames",
        }
    )
    if set(value) != expected_keys or version not in {1, 2}:
        raise ValueError("invalid animation envelope")
    if value["fps"] != expected_fps:
        raise ValueError("invalid animation metadata")
    duration = float(value["duration"])
    raw_frames = value["frames"]
    if not 0 < duration <= max_seconds or not isinstance(raw_frames, list):
        raise ValueError("invalid animation duration")
    if not raw_frames or len(raw_frames) > expected_fps * max_seconds + 2:
        raise ValueError("invalid animation frame count")
    frames: list[FaceAnimationFrame] = []
    previous = -1.0
    for raw in raw_frames:
        if not isinstance(raw, dict) or set(raw) != {"t", "mouth_open", "eye_x", "eye_y"}:
            raise ValueError("invalid animation frame")
        timestamp = float(raw["t"])
        if timestamp < previous or timestamp > duration + 0.05:
            raise ValueError("invalid frame timestamp")
        previous = timestamp
        frames.append(
            FaceAnimationFrame(
                round(timestamp, 6),
                _unit(raw["mouth_open"]),
                _signed_unit(raw["eye_x"]),
                _signed_unit(raw["eye_y"]),
            )
        )
    rig = None
    if version == 2:
        frame_count = value["frame_count"]
        face_controls = _control_names(value["face_controls"], 52)
        tongue_controls = _control_names(value["tongue_controls"], 16, allow_fewer=True)
        if (
            not isinstance(frame_count, int)
            or isinstance(frame_count, bool)
            or frame_count != len(frames)
            or set(face_controls) != set(ARKIT_FACE_CONTROLS)
            or len(set(face_controls)) != 52
            or any(name not in AUDIO2FACE_TONGUE_CONTROLS for name in tongue_controls)
        ):
            raise ValueError("invalid rig control metadata")
        width = len(face_controls) + len(tongue_controls)
        if len(binary) != frame_count * width * 4 or len(binary) > _MAX_RIG_BYTES:
            raise ValueError("invalid rig binary length")
        values = array("f")
        values.frombytes(binary)
        if sys.byteorder != "little":
            values.byteswap()
        if any(not math.isfinite(item) or not 0.0 <= item <= 1.0 for item in values):
            raise ValueError("invalid rig weight")
        rig = FaceRigAnimation(
            "float32-le-frame-major",
            expected_fps,
            frame_count,
            face_controls,
            tongue_controls,
            binary,
        )
    elif binary:
        raise ValueError("legacy animation returned unexpected rig data")
    return FaceAnimation(expected_fps, duration, tuple(frames), model_name, rig)


def _control_names(value: object, maximum: int, *, allow_fewer: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError("invalid rig control names")
    names = tuple(cast(list[str], value))
    if len(names) > maximum or (not allow_fewer and len(names) != maximum):
        raise ValueError("invalid rig control count")
    if len(names) != len(set(names)):
        raise ValueError("duplicate rig control name")
    return names


def _unit(value: object) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError("value is not numeric")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError("value is outside unit range")
    return round(number, 6)


def _signed_unit(value: object) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError("value is not numeric")
    number = float(value)
    if not -1.0 <= number <= 1.0:
        raise ValueError("value is outside signed unit range")
    return round(number, 6)


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(pcm)


def _fixed_environment(dependency_directories: tuple[Path, ...]) -> dict[str, str]:
    allowed = {
        key: value
        for key, value in os.environ.items()
        if key.casefold() in {"systemroot", "windir", "temp", "tmp", "path"}
    }
    dependency_path = os.pathsep.join(str(item) for item in dependency_directories)
    inherited = allowed.get("PATH", allowed.get("Path", ""))
    allowed["PATH"] = os.pathsep.join(item for item in (dependency_path, inherited) if item)
    return allowed


def _nvidia_gpu_available() -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())
