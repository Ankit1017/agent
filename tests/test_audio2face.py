"""Offline coverage for the bounded NVIDIA Audio2Face integration."""

from __future__ import annotations

import copy
import json
import struct
import subprocess
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_harness.application.audio2face import (
    AnimatedSpeechService,
    _resample_s16le_mono,
)
from local_harness.application.speech import SpeechService
from local_harness.bootstrap import build_animated_speech_service
from local_harness.config import Settings
from local_harness.domain.audio2face import (
    ARKIT_FACE_CONTROLS,
    AUDIO2FACE_TONGUE_CONTROLS,
    Audio2FaceStatus,
    FaceAnimation,
    FaceAnimationFrame,
    FaceAvatarAsset,
    FaceAvatarChoice,
    FaceAvatarStatus,
    FaceRigAnimation,
)
from local_harness.domain.errors import (
    Audio2FaceUnavailableError,
    Audio2FaceValidationError,
    ConfigurationError,
)
from local_harness.domain.speech import SpeechFormat, SpeechRequest, SpeechVoice
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure import audio2face as adapter_module
from local_harness.infrastructure.audio2face import NvidiaAudio2FaceAnimator
from local_harness.interfaces.web.api import create_app
from local_harness.interfaces.web.coordinator import WebRuntimeCoordinator


class FakeSynthesizer:
    """Return deterministic 8 kHz PCM and record sanitized requests."""

    def __init__(self) -> None:
        self.requests: list[SpeechRequest] = []

    def voices(self) -> tuple[SpeechVoice, ...]:
        """Return the one test voice."""
        return (
            SpeechVoice(
                "voice",
                "Test Voice",
                "English",
                SpeechFormat(8_000),
                "Test only",
                default=True,
                loaded=True,
            ),
        )

    def synthesize(self, request: SpeechRequest) -> Iterator[bytes]:
        """Return two signed 16-bit samples."""
        self.requests.append(request)
        return iter((b"\x00\x00\xff\x7f",))


class FakeAnimator:
    """Record canonical PCM and return one animation frame."""

    def __init__(self) -> None:
        self.audio: list[bytes] = []

    def status(self) -> Audio2FaceStatus:
        """Report deterministic availability."""
        return Audio2FaceStatus(True, True, True, True, True, "Ready", "mark", 60)

    def animate(self, pcm_s16le_16khz: bytes) -> FaceAnimation:
        """Record resampled PCM and return a bounded response."""
        self.audio.append(pcm_s16le_16khz)
        rig = FaceRigAnimation(
            "float32-le-frame-major",
            60,
            1,
            ARKIT_FACE_CONTROLS,
            (),
            struct.pack("<52f", *([0.25] * 52)),
        )
        return FaceAnimation(60, 0.01, (FaceAnimationFrame(0.0, 0.5),), "mark", rig)


class FakeAvatarRepository:
    """Expose one request-independent validated avatar to the API test."""

    def status(self, avatar_id: str | None = None) -> FaceAvatarStatus:
        """Report a complete canonical face rig."""
        return FaceAvatarStatus(True, "Test Face", ARKIT_FACE_CONTROLS, (), "Ready")

    def asset(self, avatar_id: str | None = None) -> FaceAvatarAsset:
        """Return deterministic bytes without a request-controlled path."""
        return FaceAvatarAsset("Test Face", "a" * 64, ARKIT_FACE_CONTROLS, (), b"glTF")

    def catalog(self) -> tuple[FaceAvatarChoice, ...]:
        """Return one deterministic selectable character."""
        return (FaceAvatarChoice("default", "Test Face", 52, 0),)

    def default_id(self) -> str:
        """Return the fake character identifier."""
        return "default"


def _service(
    avatar_repository: FakeAvatarRepository | None = None,
) -> tuple[AnimatedSpeechService, FakeSynthesizer, FakeAnimator]:
    provider = FakeSynthesizer()
    animator = FakeAnimator()
    speech = SpeechService(
        provider,
        SecretRedactor().sanitize,
        default_voice="voice",
        max_chars=5_000,
    )
    return AnimatedSpeechService(speech, animator, avatar_repository), provider, animator


def _coordinator(tmp_path: Path) -> tuple[WebRuntimeCoordinator, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    tmp_path.joinpath(".env").write_text(
        "OPENAI_API_KEY=sk-local-real-test-key\n"
        "OPENAI_BASE_URL=http://127.0.0.1:4000/v1\n"
        "OPENAI_MODEL=gpt-5.5\n"
        "HARNESS_MODELS=gpt-5.5\n"
        "SEARXNG_BASE_URL=http://127.0.0.1:8080\n",
        encoding="utf-8",
    )
    static = tmp_path / "static"
    static.mkdir()
    static.joinpath("index.html").write_text("<main>Harness</main>", encoding="utf-8")
    return WebRuntimeCoordinator(tmp_path, tmp_path / "catalog.json"), static


def test_service_redacts_resamples_and_bounds_audio() -> None:
    """The use case sends only sanitized 16 kHz PCM to the face animator."""
    service, provider, animator = _service()
    assert service.status().available is True

    result = service.generate("say sk-abcdefghijklmnop", "voice")

    assert provider.requests[0].text == "say [REDACTED]"
    assert result.redacted is True
    assert result.audio == b"\x00\x00\xff\x7f"
    with pytest.raises(Audio2FaceValidationError, match="not configured"):
        service.avatar_asset()
    assert len(animator.audio[0]) == 8
    assert result.animation.frames[0].mouth_open == 0.5

    short = AnimatedSpeechService(
        service._speech,  # noqa: SLF001 - focused duration-boundary test seam
        animator,
        max_seconds=1,
    )
    provider.synthesize = lambda request: iter((b"\x00\x00" * 8_001,))  # type: ignore[method-assign]
    with pytest.raises(Audio2FaceValidationError, match="exceeds"):
        short.generate("too long", "voice")

    with pytest.raises(ValueError, match="maximum duration"):
        AnimatedSpeechService(service._speech, animator, max_seconds=0)  # noqa: SLF001

    assert _resample_s16le_mono(b"\x00\x00", 16_000, 16_000) == b"\x00\x00"
    with pytest.raises(Audio2FaceValidationError, match="metadata"):
        _resample_s16le_mono(b"\x00", 16_000, 16_000)


def test_service_filters_optional_tongue_columns_for_the_installed_avatar() -> None:
    """Only tongue weights exposed by both Mark and the fixed GLB leave application code."""

    class TongueAnimator(FakeAnimator):
        def animate(self, pcm_s16le_16khz: bytes) -> FaceAnimation:
            self.audio.append(pcm_s16le_16khz)
            tongues = AUDIO2FACE_TONGUE_CONTROLS[:2]
            first = [0.1] * 52 + [0.7, 0.9]
            second = [0.2] * 52 + [0.6, 0.8]
            rig = FaceRigAnimation(
                "float32-le-frame-major",
                60,
                2,
                ARKIT_FACE_CONTROLS,
                tongues,
                struct.pack("<108f", *(first + second)),
            )
            frames = (FaceAnimationFrame(0.0, 0.1), FaceAnimationFrame(0.01, 0.2))
            return FaceAnimation(60, 0.02, frames, "mark", rig)

    class SelectiveAvatar(FakeAvatarRepository):
        def status(self, avatar_id: str | None = None) -> FaceAvatarStatus:
            return FaceAvatarStatus(
                True,
                "Selective Face",
                ARKIT_FACE_CONTROLS,
                AUDIO2FACE_TONGUE_CONTROLS[:1],
                "Ready",
            )

    provider = FakeSynthesizer()
    speech = SpeechService(
        provider,
        SecretRedactor().sanitize,
        default_voice="voice",
        max_chars=5_000,
    )
    service = AnimatedSpeechService(speech, TongueAnimator(), SelectiveAvatar())
    result = service.generate("hello", "voice")
    assert result.animation.rig is not None
    assert result.animation.rig.tongue_controls == AUDIO2FACE_TONGUE_CONTROLS[:1]
    assert len(result.animation.rig.weights) == 2 * 53 * 4
    assert struct.unpack("<53f", result.animation.rig.weights[: 53 * 4])[-1] == pytest.approx(0.7)


def test_adapter_uses_fixed_process_and_removes_ephemeral_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adapter validates bridge JSON and removes request WAV data afterward."""
    bridge = tmp_path / "audio2face-bridge.exe"
    model = tmp_path / "model.json"
    bridge.touch()
    model.write_text("{}", encoding="utf-8")
    runtime = tmp_path / "runtime"
    observed_input: list[Path] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        input_path = Path(command[command.index("--input") + 1])
        output_path = Path(command[command.index("--output") + 1])
        observed_input.append(input_path)
        assert input_path.is_file()
        output_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "fps": 60,
                    "duration": 0.01,
                    "frame_count": 1,
                    "face_controls": list(ARKIT_FACE_CONTROLS),
                    "tongue_controls": [],
                    "frames": [{"t": 0, "mouth_open": 0.75, "eye_x": 0, "eye_y": 0}],
                }
            ),
            encoding="utf-8",
        )
        output_path.with_suffix(".bin").write_bytes(struct.pack("<52f", *([0.25] * 52)))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(adapter_module, "_nvidia_gpu_available", lambda: True)
    monkeypatch.setattr(subprocess, "run", run)
    adapter = NvidiaAudio2FaceAnimator(bridge, model, runtime)

    animation = adapter.animate(b"\x00\x00" * 160)

    assert animation.frames[0].mouth_open == 0.75
    assert animation.rig is not None
    assert animation.rig.face_controls == ARKIT_FACE_CONTROLS
    assert observed_input and not observed_input[0].exists()
    assert list(runtime.iterdir()) == []


def test_adapter_uses_request_unique_temporary_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent adapter calls cannot observe or remove another request's files."""
    bridge = tmp_path / "bridge.exe"
    model = tmp_path / "model.json"
    bridge.touch()
    model.touch()
    parents: list[Path] = []
    gate = threading.Barrier(2)

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        output = Path(command[command.index("--output") + 1])
        parents.append(output.parent)
        gate.wait(timeout=5)
        output.write_text(
            json.dumps(
                {
                    "version": 1,
                    "fps": 60,
                    "duration": 0.01,
                    "frames": [{"t": 0, "mouth_open": 0.1, "eye_x": 0, "eye_y": 0}],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(adapter_module, "_nvidia_gpu_available", lambda: True)
    monkeypatch.setattr(subprocess, "run", run)
    runtime = tmp_path / "runtime"
    adapter = NvidiaAudio2FaceAnimator(bridge, model, runtime)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(adapter.animate, (b"\x00\x00", b"\x01\x00")))
    assert len(results) == 2
    assert len(set(parents)) == 2
    assert all(not parent.exists() for parent in parents)


def test_adapter_status_and_safe_process_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Availability explains missing pieces and process failures reveal no local paths."""
    bridge = tmp_path / "bridge.exe"
    model = tmp_path / "model.json"
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(adapter_module, "_nvidia_gpu_available", lambda: False)
    missing = NvidiaAudio2FaceAnimator(bridge, model, runtime)
    status = missing.status()
    assert status.available is False
    assert "compatible NVIDIA GPU/driver" in status.setup
    assert str(tmp_path) not in status.setup

    bridge.touch()
    model.touch()
    disabled = NvidiaAudio2FaceAnimator(bridge, model, runtime, enabled=False)
    assert "HARNESS_AUDIO2FACE_ENABLED" in disabled.status().setup

    monkeypatch.setattr(adapter_module, "_nvidia_gpu_available", lambda: True)
    ready = NvidiaAudio2FaceAnimator(bridge, model, runtime)
    assert ready.status().available is True
    with pytest.raises(Audio2FaceUnavailableError, match="empty"):
        ready.animate(b"")

    def timeout(*_: object, **__: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired("bridge", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(Audio2FaceUnavailableError, match="could not complete") as caught:
        ready.animate(b"\x00\x00")
    assert str(tmp_path) not in str(caught.value)


def test_adapter_rejects_invalid_bridge_envelopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed and excessive native results become bounded provider errors."""
    bridge = tmp_path / "bridge.exe"
    model = tmp_path / "model.json"
    bridge.touch()
    model.touch()
    monkeypatch.setattr(adapter_module, "_nvidia_gpu_available", lambda: True)

    def invalid(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        output = Path(command[command.index("--output") + 1])
        output.write_text('{"unexpected": true}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", invalid)
    adapter = NvidiaAudio2FaceAnimator(bridge, model, tmp_path / "runtime")
    with pytest.raises(Audio2FaceUnavailableError, match="invalid animation"):
        adapter.animate(b"\x00\x00")


def test_bridge_envelope_validation_rejects_malformed_metadata_and_weights() -> None:
    """Every packed-rig metadata and numeric boundary fails closed."""
    legacy: dict[str, object] = {
        "version": 1,
        "fps": 60,
        "duration": 0.1,
        "frames": [{"t": 0, "mouth_open": 0.1, "eye_x": 0, "eye_y": 0}],
    }
    malformed: list[tuple[object, bytes, str]] = [(None, b"", "envelope")]
    for key, value, message in (
        ("fps", 30, "metadata"),
        ("duration", 0, "duration"),
        ("frames", [], "frame count"),
        ("frames", [{}], "animation frame"),
        (
            "frames",
            [{"t": 0.2, "mouth_open": 0.1, "eye_x": 0, "eye_y": 0}],
            "timestamp",
        ),
        (
            "frames",
            [{"t": 0, "mouth_open": "bad", "eye_x": 0, "eye_y": 0}],
            "not numeric",
        ),
        (
            "frames",
            [{"t": 0, "mouth_open": 2, "eye_x": 0, "eye_y": 0}],
            "unit range",
        ),
        (
            "frames",
            [{"t": 0, "mouth_open": 0, "eye_x": "bad", "eye_y": 0}],
            "not numeric",
        ),
        (
            "frames",
            [{"t": 0, "mouth_open": 0, "eye_x": 2, "eye_y": 0}],
            "signed unit",
        ),
    ):
        payload = copy.deepcopy(legacy)
        payload[key] = value
        malformed.append((payload, b"", message))
    malformed.append((legacy, b"unexpected", "unexpected rig"))

    rig: dict[str, object] = {
        "version": 2,
        "fps": 60,
        "duration": 0.1,
        "frame_count": 1,
        "face_controls": list(ARKIT_FACE_CONTROLS),
        "tongue_controls": [],
        "frames": legacy["frames"],
    }
    invalid_count = copy.deepcopy(rig)
    invalid_count["frame_count"] = True
    malformed.append((invalid_count, struct.pack("<52f", *([0.1] * 52)), "control metadata"))

    bad_names = copy.deepcopy(rig)
    bad_names["face_controls"] = "invalid"
    malformed.append((bad_names, b"", "control names"))
    short_names = copy.deepcopy(rig)
    short_names["face_controls"] = list(ARKIT_FACE_CONTROLS[:-1])
    malformed.append((short_names, b"", "control count"))
    duplicate_names = copy.deepcopy(rig)
    duplicate_names["face_controls"] = [ARKIT_FACE_CONTROLS[0]] * 52
    malformed.append((duplicate_names, b"", "duplicate"))
    malformed.append((rig, b"", "binary length"))
    malformed.append((rig, struct.pack("<52f", *([float("nan")] * 52)), "rig weight"))

    for candidate, binary, message in malformed:
        with pytest.raises(ValueError, match=message):
            adapter_module._parse_animation(candidate, binary, 60, 60, "mark")


def test_gpu_probe_handles_success_and_missing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """The safe readiness probe handles both driver output and missing utilities."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, b"RTX", b""),
    )
    assert adapter_module._nvidia_gpu_available() is True

    def missing(*_: object, **__: object) -> subprocess.CompletedProcess[bytes]:
        raise OSError("missing")

    monkeypatch.setattr(subprocess, "run", missing)
    assert adapter_module._nvidia_gpu_available() is False


def test_bootstrap_requires_tts_for_audio2face(tmp_path: Path) -> None:
    """Composition occurs only in bootstrap and cannot bypass the redacting speech service."""
    (tmp_path / ".env").write_text("OPENAI_API_KEY=real\n", encoding="utf-8")
    settings = Settings.load(tmp_path)
    assert build_animated_speech_service(tmp_path, settings, None) is None
    with pytest.raises(ConfigurationError, match="requires HARNESS_TTS_ENABLED"):
        build_animated_speech_service(
            tmp_path,
            replace(settings, audio2face_enabled=True),
            None,
        )


def test_audio2face_api_is_protected_and_returns_closed_bounded_data(
    tmp_path: Path,
) -> None:
    """Status is safe and generation retains browser Origin and CSRF controls."""
    coordinator, static = _coordinator(tmp_path)
    service, _, _ = _service(FakeAvatarRepository())
    app = create_app(
        coordinator,
        static,
        animated_speech_service=service,
        origins=frozenset({"http://testserver"}),
        trusted_hosts=["testserver"],
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/speech/audio2face/avatar").status_code == 403
        bootstrap = client.get("/api/v1/bootstrap").json()
        headers = {
            "Origin": "http://testserver",
            "X-Harness-CSRF": bootstrap["csrf_token"],
        }
        assert bootstrap["audio2face_enabled"] is True
        status = client.get("/api/v1/speech/audio2face/status").json()
        assert status["available"] is True
        assert status["default_avatar_id"] == "default"
        assert status["avatars"][0]["name"] == "Test Face"
        avatar = client.get("/api/v1/speech/audio2face/avatar")
        assert avatar.status_code == 200
        assert avatar.content == b"glTF"
        assert avatar.headers["content-type"] == "model/gltf-binary"
        assert "path" not in avatar.headers
        selected_avatar = client.get("/api/v1/speech/audio2face/avatars/default")
        assert selected_avatar.status_code == 200
        assert selected_avatar.content == b"glTF"
        assert (
            client.post(
                "/api/v1/speech/audio2face/generate",
                json={"text": "hello", "voice_id": "voice", "rate": 1},
            ).status_code
            == 403
        )
        response = client.post(
            "/api/v1/speech/audio2face/generate",
            headers=headers,
            json={"text": "hello", "voice_id": "voice", "rate": 1},
        )
        assert response.status_code == 200
        assert response.json()["animation"]["model"] == "mark"
        assert response.json()["animation"]["rig"]["frame_count"] == 1
        assert response.json()["audio_base64"] == "AAD/fw=="

        invalid = client.post(
            "/api/v1/speech/audio2face/generate",
            headers=headers,
            json={"text": "hello", "voice_id": "voice", "rate": 1, "path": "bad"},
        )
        assert invalid.status_code == 422
        invalid_avatar = client.post(
            "/api/v1/speech/audio2face/generate",
            headers=headers,
            json={"text": "hello", "voice_id": "voice", "avatar_id": "../face"},
        )
        assert invalid_avatar.status_code == 422


def test_audio2face_api_disabled_status_is_actionable(tmp_path: Path) -> None:
    """A disabled installation leaves Speech reachable with bounded setup guidance."""
    coordinator, static = _coordinator(tmp_path)
    app = create_app(
        coordinator,
        static,
        origins=frozenset({"http://testserver"}),
        trusted_hosts=["testserver"],
    )
    with TestClient(app) as client:
        bootstrap = client.get("/api/v1/bootstrap").json()
        headers = {
            "Origin": "http://testserver",
            "X-Harness-CSRF": bootstrap["csrf_token"],
        }
        status = client.get("/api/v1/speech/audio2face/status").json()
        assert status["available"] is False
        assert "setup-audio2face.ps1" in status["setup"]
        response = client.post(
            "/api/v1/speech/audio2face/generate",
            headers=headers,
            json={"text": "hello", "voice_id": "voice", "rate": 1},
        )
        assert response.status_code == 503
        assert str(tmp_path) not in response.text
