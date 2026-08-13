"""Offline tests for bounded local wake-word and microphone transcription."""

from __future__ import annotations

import struct
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from local_harness.application.speech_input import SpeechInputService
from local_harness.domain.errors import (
    SpeechInputBusyError,
    SpeechInputValidationError,
)
from local_harness.domain.speech_input import RecognitionResult, SpeechInputEvent
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.speech_input import (
    FasterWhisperSpeechRecognizer,
    SherpaWakeWordDetector,
)
from local_harness.interfaces.web.api import create_app
from local_harness.interfaces.web.coordinator import WebRuntimeCoordinator


class FakeWakeStream:
    """Detect one distinctive fake PCM frame."""

    def __init__(self) -> None:
        self.closed = False
        self.resets = 0

    def accept(self, chunk: bytes) -> bool:
        """Treat the configured marker as the wake phrase."""
        return chunk.startswith(struct.pack("<h", 2_000))

    def reset(self) -> None:
        """Record decoder reset calls."""
        self.resets += 1

    def close(self) -> None:
        """Record cleanup."""
        self.closed = True


class FakeWakeDetector:
    """Return observable fake wake streams."""

    def __init__(self) -> None:
        self.streams: list[FakeWakeStream] = []

    def open_stream(self) -> FakeWakeStream:
        """Create one fake stream."""
        stream = FakeWakeStream()
        self.streams.append(stream)
        return stream


class FakeRecognizer:
    """Return queued transcripts while retaining only call sizes."""

    def __init__(self, results: Sequence[RecognitionResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[int, tuple[str, ...]]] = []

    def transcribe(self, pcm: bytes, languages: tuple[str, ...]) -> RecognitionResult:
        """Return the next local fake transcript."""
        self.calls.append((len(pcm), languages))
        return self.results.pop(0)


def _pcm(sample: int, milliseconds: int = 100) -> bytes:
    return struct.pack("<h", sample) * (16 * milliseconds)


def _service(
    results: Sequence[RecognitionResult],
    *,
    silence_ms: int = 300,
    clock: Callable[[], float] | None = None,
) -> tuple[SpeechInputService, FakeWakeDetector, FakeRecognizer]:
    detector = FakeWakeDetector()
    recognizer = FakeRecognizer(results)
    redactor = SecretRedactor(("configured-secret",))
    return (
        SpeechInputService(
            detector,
            recognizer,
            redactor.sanitize,
            wake_phrase="hey buddy",
            languages=("en", "hi"),
            max_seconds=15,
            silence_ms=silence_ms,
            clock=clock or time.monotonic,
        ),
        detector,
        recognizer,
    )


def test_wake_command_transcribes_sanitizes_and_rearms() -> None:
    """A same-utterance wake command returns one bounded sanitized transcript."""
    service, detector, recognizer = _service(
        [RecognitionResult("Hey Buddy, send configured-secret please", "en")]
    )
    session = service.open_session("wake")
    assert service.audio_format.sample_rate == 16_000
    assert session.accept(_pcm(2_000))[0].type == "wake_detected"
    events = session.accept(_pcm(0, 300))
    assert events[-1].type == "transcribing"
    result = session.transcribe_pending()
    assert result.transcript is not None
    assert result.transcript.text == "send [REDACTED] please"
    assert result.transcript.language == "en"
    assert result.transcript.redacted is True
    assert recognizer.calls[0][1] == ("en", "hi")
    assert session.rearm()[0].reason == "wake"
    session.close()
    assert detector.streams[0].closed is True


def test_standalone_wake_waits_for_followup_and_tap_can_finish() -> None:
    """Wake-only text resumes follow-up capture while tap mode supports early finish."""
    service, _, _ = _service(
        [RecognitionResult("Hey Buddy", "en"), RecognitionResult("नमस्ते", "hi")]
    )
    wake = service.open_session("wake")
    wake.accept(_pcm(2_000))
    wake.accept(_pcm(0, 300))
    assert wake.transcribe_pending().reason == "followup"
    wake.accept(_pcm(1_500))
    assert wake.finish()[0].type == "transcribing"
    hindi = wake.transcribe_pending()
    assert hindi.transcript is not None and hindi.transcript.language == "hi"
    wake.close()

    tap_service, _, _ = _service([RecognitionResult("tap message", "en")])
    tap = tap_service.open_session("tap")
    tap.accept(_pcm(1_500))
    assert tap.finish()[0].reason == "manual_stop"
    assert tap.transcribe_pending().transcript is not None
    tap.close()


def test_global_busy_bounds_invalid_frames_and_releases() -> None:
    """Only one session runs and malformed PCM cannot reach a recognizer."""
    service, _, _ = _service([RecognitionResult("unused", "en")])
    first = service.open_session("tap")
    with pytest.raises(SpeechInputBusyError):
        service.open_session("wake")
    with pytest.raises(SpeechInputValidationError, match="PCM"):
        first.accept(b"odd")
    first.close()
    second = service.open_session("wake")
    second.close()


def test_max_duration_cancel_pause_and_no_speech_are_bounded() -> None:
    """Duration, empty input, cancellation, and paused frames have deterministic results."""
    now = [0.0]

    def clock() -> float:
        return now[0]

    service, _, _ = _service([RecognitionResult("bounded", "en")], clock=clock)
    session = service.open_session("tap")
    assert session.finish()[0].reason == "no_speech"
    session.begin_tap()
    chunk = _pcm(1_000, 1_000)
    events: tuple[SpeechInputEvent, ...] = ()
    for _ in range(15):
        now[0] += 1
        events = session.accept(chunk)
    assert events[-1].reason == "max_duration"
    assert session.transcribe_pending().transcript is not None
    assert session.pause()[0].type == "paused"
    assert session.accept(_pcm(1_000)) == ()
    assert session.cancel()[0].type == "cancelled"
    session.close()


def test_protected_microphone_websocket_streams_bounded_events(tmp_path: Path) -> None:
    """Cookie, Origin, CSRF, binary PCM, and transcript events share one protected socket."""
    tmp_path.joinpath(".env").write_text(
        "OPENAI_API_KEY=real\nOPENAI_MODEL=model-a\nHARNESS_MODELS=model-a\n",
        encoding="utf-8",
    )
    static = tmp_path / "static"
    static.mkdir()
    static.joinpath("index.html").write_text("<main>Harness</main>", encoding="utf-8")
    coordinator = WebRuntimeCoordinator(tmp_path, tmp_path / "catalog.json")
    service, _, _ = _service([RecognitionResult("spoken secret", "en")])
    app = create_app(
        coordinator,
        static,
        speech_input_service=service,
        origins=frozenset({"http://testserver"}),
        trusted_hosts=["testserver"],
    )
    with TestClient(app) as client:
        bootstrap = client.get("/api/v1/bootstrap").json()
        status = client.get("/api/v1/speech/input/status")
        assert status.json()["audio_format"]["sample_rate"] == 16_000
        with client.websocket_connect(
            "/api/v1/speech/input/stream", headers={"Origin": "http://testserver"}
        ) as socket:
            socket.send_json(
                {
                    "type": "start",
                    "csrf_token": bootstrap["csrf_token"],
                    "mode": "tap",
                    "sample_rate": 16000,
                    "channels": 1,
                    "sample_width": 2,
                    "encoding": "s16le",
                }
            )
            assert socket.receive_json()["type"] == "ready"
            socket.send_bytes(_pcm(1_500))
            assert socket.receive_json()["type"] == "speech_started"
            socket.send_json({"type": "finish"})
            assert socket.receive_json()["type"] == "transcribing"
            transcript = socket.receive_json()
            assert transcript["type"] == "transcript"
            assert transcript["transcript"]["text"] == "spoken secret"
            socket.send_json({"type": "close"})

        with client.websocket_connect(
            "/api/v1/speech/input/stream", headers={"Origin": "http://testserver"}
        ) as invalid:
            invalid.send_json(
                {
                    "type": "start",
                    "csrf_token": "wrong-but-long-enough",
                    "mode": "wake",
                    "sample_rate": 16000,
                    "channels": 1,
                    "sample_width": 2,
                    "encoding": "s16le",
                }
            )
            assert invalid.receive_json()["type"] == "error"


def test_sherpa_adapter_uses_preloaded_local_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Sherpa boundary passes fixed local artifacts and isolates stream state."""
    model = tmp_path / "wake"
    model.mkdir()
    for name in (
        "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        "tokens.txt",
        "hey-buddy.txt",
    ):
        model.joinpath(name).write_bytes(b"local")

    class ProviderStream:
        def __init__(self) -> None:
            self.ready = True
            self.samples = 0

        def accept_waveform(self, sample_rate: int, samples: object) -> None:
            assert sample_rate == 16_000
            self.samples = len(samples)  # type: ignore[arg-type]

    class Spotter:
        options: dict[str, object] = {}

        def __init__(self, **options: object) -> None:
            Spotter.options = options

        def create_stream(self) -> ProviderStream:
            return ProviderStream()

        def is_ready(self, stream: ProviderStream) -> bool:
            return stream.ready

        def decode_stream(self, stream: ProviderStream) -> None:
            stream.ready = False

        def get_result(self, stream: ProviderStream) -> str:
            return "HEY BUDDY" if stream.samples else ""

    provider = SimpleNamespace(KeywordSpotter=Spotter)
    monkeypatch.setattr(
        "local_harness.infrastructure.speech_input.importlib.import_module",
        lambda name: provider if name == "sherpa_onnx" else __import__(name),
    )
    detector = SherpaWakeWordDetector(model, model / "hey-buddy.txt")
    assert Spotter.options["provider"] == "cpu"
    stream = detector.open_stream()
    assert stream.accept(_pcm(2_000)) is True
    stream.reset()
    stream.close()
    assert stream.accept(_pcm(2_000)) is False


def test_faster_whisper_adapter_transcribes_only_in_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The recognizer loads a fixed local directory and accepts an in-memory PCM array."""
    model = tmp_path / "whisper-small"
    model.mkdir()
    model.joinpath("model.bin").write_bytes(b"local")

    class WhisperModel:
        init: tuple[tuple[object, ...], dict[str, object]]

        def __init__(self, *args: object, **kwargs: object) -> None:
            WhisperModel.init = (args, kwargs)

        def transcribe(
            self, audio: object, **kwargs: object
        ) -> tuple[list[SimpleNamespace], SimpleNamespace]:
            assert len(audio) > 0  # type: ignore[arg-type]
            assert kwargs["vad_filter"] is True
            return [
                SimpleNamespace(text=" Hello "),
                SimpleNamespace(text="buddy"),
            ], SimpleNamespace(language="en")

    provider = SimpleNamespace(WhisperModel=WhisperModel)
    monkeypatch.setattr(
        "local_harness.infrastructure.speech_input.importlib.import_module",
        lambda name: provider if name == "faster_whisper" else __import__(name),
    )
    recognizer = FasterWhisperSpeechRecognizer(model)
    result = recognizer.transcribe(_pcm(1_500), ("en", "hi"))
    assert result == RecognitionResult("Hello buddy", "en")
    assert WhisperModel.init[1]["local_files_only"] is True
