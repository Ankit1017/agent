"""In-process Sherpa-ONNX wake word and Faster Whisper transcription adapters."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from local_harness.domain.errors import ConfigurationError, SpeechInputUnavailableError
from local_harness.domain.speech_input import RecognitionResult


class SherpaWakeWordDetector:
    """Preload one CPU keyword model and create isolated decoding streams."""

    def __init__(self, model_directory: Path, keywords_file: Path) -> None:
        """Validate fixed local artifacts and construct the Sherpa spotter."""
        names = {
            "encoder": "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
            "decoder": "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
            "joiner": "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
            "tokens": "tokens.txt",
        }
        paths = {key: model_directory / value for key, value in names.items()}
        if not keywords_file.is_file() or any(not path.is_file() for path in paths.values()):
            raise ConfigurationError(
                "Local wake-word models are missing. Run scripts/setup-speech-input.ps1."
            )
        try:
            sherpa_onnx = importlib.import_module("sherpa_onnx")

            self._spotter: Any = sherpa_onnx.KeywordSpotter(
                tokens=str(paths["tokens"]),
                encoder=str(paths["encoder"]),
                decoder=str(paths["decoder"]),
                joiner=str(paths["joiner"]),
                keywords_file=str(keywords_file),
                num_threads=1,
                sample_rate=16_000,
                feature_dim=80,
                max_active_paths=4,
                num_trailing_blanks=1,
                keywords_score=2.0,
                keywords_threshold=0.25,
                provider="cpu",
            )
        except ConfigurationError:
            raise
        except Exception as exc:
            raise ConfigurationError("The local wake-word model could not be loaded") from exc

    def open_stream(self) -> SherpaWakeWordStream:
        """Return a stream over the already loaded Sherpa model."""
        return SherpaWakeWordStream(self._spotter)


class SherpaWakeWordStream:
    """Adapt signed PCM chunks to one Sherpa keyword decoder stream."""

    def __init__(self, spotter: Any) -> None:
        """Create a provider stream without copying the loaded model."""
        self._spotter = spotter
        self._stream: Any = spotter.create_stream()
        self._closed = False

    def accept(self, chunk: bytes) -> bool:
        """Decode one PCM chunk and report a completed keyword."""
        if self._closed:
            return False
        try:
            import numpy as np

            samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32_768.0
            self._stream.accept_waveform(16_000, samples)
            while self._spotter.is_ready(self._stream):
                self._spotter.decode_stream(self._stream)
            result = self._spotter.get_result(self._stream)
            # Sherpa 1.13 returns the keyword directly as a string. Older
            # compatible builds exposed a small result object instead.
            if isinstance(result, str):
                return bool(result.strip())
            return bool(getattr(result, "keyword", ""))
        except Exception as exc:
            raise SpeechInputUnavailableError("Local wake-word detection failed") from exc

    def reset(self) -> None:
        """Replace only the lightweight provider stream state."""
        if not self._closed:
            self._stream = self._spotter.create_stream()

    def close(self) -> None:
        """Drop references to stream-specific native resources."""
        self._closed = True
        self._stream = None


class FasterWhisperSpeechRecognizer:
    """Preload a local multilingual Faster Whisper model for CPU INT8 inference."""

    def __init__(self, model_directory: Path) -> None:
        """Load only an explicit local model directory with downloads disabled."""
        if not model_directory.is_dir() or not model_directory.joinpath("model.bin").is_file():
            raise ConfigurationError(
                "Local transcription models are missing. Run scripts/setup-speech-input.ps1."
            )
        try:
            WhisperModel = importlib.import_module("faster_whisper").WhisperModel

            self._model: Any = WhisperModel(
                str(model_directory),
                device="cpu",
                compute_type="int8",
                local_files_only=True,
                cpu_threads=4,
            )
        except Exception as exc:
            raise ConfigurationError("The local transcription model could not be loaded") from exc

    def transcribe(self, pcm: bytes, languages: tuple[str, ...]) -> RecognitionResult:
        """Transcribe one in-memory 16 kHz utterance without temporary files."""
        try:
            import numpy as np

            audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32_768.0
            segments, info = self._model.transcribe(
                audio,
                beam_size=1,
                best_of=1,
                language=None,
                task="transcribe",
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
                condition_on_previous_text=False,
                word_timestamps=False,
                hotwords="Hey Buddy",
            )
            text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
            language = str(getattr(info, "language", ""))
            if language not in languages:
                return RecognitionResult(text, language)
            return RecognitionResult(text, language)
        except Exception as exc:
            raise SpeechInputUnavailableError("Local speech transcription failed") from exc
