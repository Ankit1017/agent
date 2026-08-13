"""Offline coverage for local streaming speech boundaries."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from piper.config import SynthesisConfig

from local_harness.application.speech import SpeechService
from local_harness.domain.errors import (
    ConfigurationError,
    SpeechBusyError,
    SpeechUnavailableError,
    SpeechValidationError,
)
from local_harness.domain.speech import SpeechFormat, SpeechRequest, SpeechVoice
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.piper_speech import PiperSpeechSynthesizer
from local_harness.interfaces.web.api import create_app
from local_harness.interfaces.web.coordinator import WebRuntimeCoordinator

VOICE_IDS = (
    "en_US-lessac-medium",
    "hi_IN-priyamvada-medium",
    "hi_IN-rohan-medium",
)


@dataclass
class FakeChunk:
    """One Piper-compatible fake PCM chunk."""

    audio_int16_bytes: bytes
    sample_rate: int = 22_050
    sample_width: int = 2
    sample_channels: int = 1


class FakeVoice:
    """Record Piper requests and yield deterministic PCM."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, float]] = []
        self.closed = False

    def synthesize(
        self, text: str, syn_config: SynthesisConfig | None = None
    ) -> Iterator[FakeChunk]:
        """Yield two chunks and observe cancellation in ``finally``."""
        assert syn_config is not None
        length_scale = cast(float, syn_config.length_scale)
        self.requests.append((text, length_scale))
        try:
            yield FakeChunk(b"\x01\x00")
            yield FakeChunk(b"\x02\x00")
        finally:
            self.closed = True


class FakeSynthesizer:
    """Provider-neutral speech fake for service and API tests."""

    def __init__(self) -> None:
        self.requests: list[SpeechRequest] = []

    def voices(self) -> tuple[SpeechVoice, ...]:
        """Return one deterministic voice."""
        return (
            SpeechVoice(
                "voice",
                "Test Voice",
                "Test",
                SpeechFormat(22_050),
                "Test-only voice",
                loaded=True,
            ),
        )

    def synthesize(self, request: SpeechRequest) -> Iterator[bytes]:
        """Record the sanitized request and yield PCM."""
        self.requests.append(request)
        return iter((b"\x01\x00", b"\x02\x00"))


def _model_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "models"
    directory.mkdir()
    for voice_id in VOICE_IDS:
        directory.joinpath(f"{voice_id}.onnx").touch()
        directory.joinpath(f"{voice_id}.onnx.json").touch()
    return directory


def _service(provider: FakeSynthesizer | None = None) -> SpeechService:
    return SpeechService(
        provider or FakeSynthesizer(),
        SecretRedactor().sanitize,
        default_voice="voice",
        max_chars=5_000,
    )


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


def test_service_bounds_allowlist_rate_and_redaction() -> None:
    """The use case rejects invalid input and never restores credential text."""
    provider = FakeSynthesizer()
    service = _service(provider)
    for text in ("", "x" * 5_001):
        with pytest.raises(SpeechValidationError):
            service.synthesize(text, "voice")
    with pytest.raises(SpeechValidationError, match="Voice"):
        service.synthesize("hello", "other")
    with pytest.raises(SpeechValidationError, match="rate"):
        service.synthesize("hello", "voice", 1.51)

    stream = service.synthesize("say sk-abcdefghijklmnop safely", "voice", 1.25)
    assert stream.redacted is True
    assert b"".join(stream.chunks) == b"\x01\x00\x02\x00"
    assert provider.requests == [SpeechRequest("say [REDACTED] safely", "voice", 1.25)]


def test_piper_preloads_default_lazily_caches_and_maps_rate(tmp_path: Path) -> None:
    """Only English starts warm; Hindi loads once and uses Piper length scale."""
    loaded: list[str] = []
    voices: dict[str, FakeVoice] = {}

    def load(path: Path) -> FakeVoice:
        loaded.append(path.stem)
        voices[path.stem] = FakeVoice()
        return voices[path.stem]

    adapter = PiperSpeechSynthesizer(
        _model_directory(tmp_path), VOICE_IDS, VOICE_IDS[0], voice_loader=load
    )
    assert loaded == [VOICE_IDS[0]]
    assert [voice.loaded for voice in adapter.voices()] == [True, False, False]

    assert list(adapter.synthesize(SpeechRequest("namaste", VOICE_IDS[1], 1.25)))
    assert list(adapter.synthesize(SpeechRequest("phir", VOICE_IDS[1], 1.0)))
    assert loaded == [VOICE_IDS[0], VOICE_IDS[1]]
    assert voices[VOICE_IDS[1]].requests == [("namaste", 0.8), ("phir", 1.0)]


def test_piper_rejects_busy_and_releases_on_cancellation(tmp_path: Path) -> None:
    """A live iterator excludes concurrent work and close releases it immediately."""
    voice = FakeVoice()
    adapter = PiperSpeechSynthesizer(
        _model_directory(tmp_path),
        (VOICE_IDS[0],),
        VOICE_IDS[0],
        voice_loader=lambda _: voice,
    )
    stream = adapter.synthesize(SpeechRequest("first", VOICE_IDS[0]))
    assert next(stream) == b"\x01\x00"
    with pytest.raises(SpeechBusyError):
        adapter.synthesize(SpeechRequest("second", VOICE_IDS[0]))
    stream.close()
    assert voice.closed is True
    assert list(adapter.synthesize(SpeechRequest("third", VOICE_IDS[0])))


def test_piper_requires_only_default_at_startup(tmp_path: Path) -> None:
    """Missing default artifacts fail startup while lazy voice artifacts fail on selection."""
    directory = tmp_path / "models"
    directory.mkdir()
    with pytest.raises(ConfigurationError, match="default Piper voice is missing"):
        PiperSpeechSynthesizer(
            directory, VOICE_IDS, VOICE_IDS[0], voice_loader=lambda _: FakeVoice()
        )

    directory.joinpath(f"{VOICE_IDS[0]}.onnx").touch()
    directory.joinpath(f"{VOICE_IDS[0]}.onnx.json").touch()
    adapter = PiperSpeechSynthesizer(
        directory, VOICE_IDS, VOICE_IDS[0], voice_loader=lambda _: FakeVoice()
    )
    with pytest.raises(SpeechUnavailableError, match="not installed"):
        adapter.synthesize(SpeechRequest("Hindi", VOICE_IDS[1]))


def test_speech_api_security_schema_metadata_and_disabled_state(tmp_path: Path) -> None:
    """The internal endpoint keeps browser protections and bounded response metadata."""
    coordinator, static = _coordinator(tmp_path)
    app = create_app(
        coordinator,
        static,
        speech_service=_service(),
        origins=frozenset({"http://testserver"}),
        trusted_hosts=["testserver"],
    )
    with TestClient(app) as client:
        bootstrap = client.get("/api/v1/bootstrap")
        token = bootstrap.json()["csrf_token"]
        headers = {"Origin": "http://testserver", "X-Harness-CSRF": token}
        assert client.get("/api/v1/speech/voices").json()[0]["voice_id"] == "voice"
        assert (
            client.post(
                "/api/v1/speech/stream", json={"text": "hello", "voice_id": "voice"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/speech/stream",
                headers=headers,
                json={"text": "hello", "voice_id": "voice", "rate": 1, "extra": True},
            ).status_code
            == 422
        )
        response = client.post(
            "/api/v1/speech/stream",
            headers=headers,
            json={"text": "sk-abcdefghijklmnop", "voice_id": "voice", "rate": 1},
        )
        assert response.content == b"\x01\x00\x02\x00"
        assert response.headers["x-speech-sample-rate"] == "22050"
        assert response.headers["x-speech-redacted"] == "true"
        assert "sk-" not in response.text

    disabled_coordinator, disabled_static = _coordinator(tmp_path / "disabled")
    disabled_app = create_app(disabled_coordinator, disabled_static, trusted_hosts=["testserver"])
    with TestClient(disabled_app) as client:
        assert client.get("/api/v1/bootstrap").json()["speech_enabled"] is False
        assert client.get("/api/v1/speech/voices").status_code == 503
