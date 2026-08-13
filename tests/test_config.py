"""Tests for configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_harness.config import Settings
from local_harness.domain.errors import ConfigurationError


def test_settings_loads_dotenv_and_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid local key composes default gateway settings."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-real-local-value\n", encoding="utf-8")

    settings = Settings.load(tmp_path)

    assert settings.api_key == "sk-real-local-value"
    assert settings.base_url == "http://localhost:4000/v1"
    assert settings.model == "gpt-oss:20b"
    assert settings.models == ("gpt-oss:20b",)
    assert settings.max_turns == 20
    assert settings.max_turns_source == "default"
    assert settings.context_max_chars == 30_000
    assert settings.batch_max_files == 8
    assert settings.patch_max_chars == 100_000
    assert settings.web_provider == "searxng"
    assert settings.searxng_base_url == "http://127.0.0.1:8080"
    assert settings.web_max_results == 8
    assert settings.web_max_pages == 5
    assert settings.web_page_max_chars == 12_000
    assert settings.web_total_max_chars == 30_000
    assert settings.session_token_budget == 0
    assert settings.token_warning_percent == 80
    assert settings.enabled_plugins == ()
    assert settings.tool_profile == "auto"
    assert settings.tool_schema_limit == 8
    assert settings.tool_activation_limit == 5
    assert settings.project_index_enabled is True
    assert settings.embedding_model == "embeddinggemma"
    assert settings.retrieval_max_files == 6
    assert settings.workflow_mode == "auto"
    assert settings.workflow_confidence_min == 0.60
    assert settings.workflow_stage_max_attempts == 2
    assert settings.evaluation_enabled is True
    assert settings.evaluation_capture_sessions is True
    assert settings.evaluation_max_trace_chars == 30_000
    assert settings.evaluation_live is False
    assert settings.evaluation_min_comparison_cases == 10
    assert settings.candidate_proposals_enabled is True
    assert settings.tts_enabled is False
    assert settings.tts_default_voice == "en_US-lessac-medium"
    assert settings.tts_max_chars == 5_000
    assert settings.tts_voices == (
        "en_US-lessac-medium",
        "hi_IN-priyamvada-medium",
        "hi_IN-rohan-medium",
    )
    assert settings.stt_enabled is False
    assert settings.stt_model == "small"
    assert settings.stt_languages == ("en", "hi")
    assert settings.stt_wake_phrase == "hey buddy"
    assert settings.stt_max_seconds == 15
    assert settings.stt_silence_ms == 800


def test_settings_validates_speech_configuration(tmp_path: Path) -> None:
    """Speech uses a bounded exact voice allowlist with a member default."""
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=real\n"
        "HARNESS_TTS_ENABLED=true\n"
        "HARNESS_TTS_DEFAULT_VOICE=voice-b\n"
        "HARNESS_TTS_VOICES=voice-a,voice-b,voice-a\n"
        "HARNESS_TTS_MAX_CHARS=4000\n",
        encoding="utf-8",
    )
    settings = Settings.load(tmp_path)
    assert settings.tts_enabled is True
    assert settings.tts_voices == ("voice-a", "voice-b")
    assert settings.tts_default_voice == "voice-b"
    assert settings.tts_max_chars == 4_000

    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=real\nHARNESS_TTS_DEFAULT_VOICE=missing\nHARNESS_TTS_VOICES=voice-a\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="included"):
        Settings.load(tmp_path)


def test_settings_validates_local_speech_input_configuration(tmp_path: Path) -> None:
    """Local STT has fixed models, languages, wake phrase, and recording bounds."""
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=real\n"
        "HARNESS_STT_ENABLED=true\n"
        "HARNESS_STT_MODEL=small\n"
        "HARNESS_STT_LANGUAGES=en,hi\n"
        "HARNESS_STT_WAKE_PHRASE=Hey Buddy\n"
        "HARNESS_STT_MAX_SECONDS=12\n"
        "HARNESS_STT_SILENCE_MS=900\n",
        encoding="utf-8",
    )
    settings = Settings.load(tmp_path)
    assert settings.stt_enabled is True
    assert settings.stt_languages == ("en", "hi")
    assert settings.stt_max_seconds == 12
    assert settings.stt_silence_ms == 900

    for line, error in (
        ("HARNESS_STT_MODEL=large", "MODEL"),
        ("HARNESS_STT_LANGUAGES=en,fr", "LANGUAGES"),
        ("HARNESS_STT_WAKE_PHRASE=computer", "WAKE_PHRASE"),
        ("HARNESS_STT_MAX_SECONDS=16", "MAX_SECONDS"),
        ("HARNESS_STT_SILENCE_MS=200", "SILENCE_MS"),
    ):
        (tmp_path / ".env").write_text(f"OPENAI_API_KEY=real\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match=error):
            Settings.load(tmp_path)


def test_settings_resolves_dotenv_and_process_turn_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Process environment takes precedence over the dotenv call limit."""
    (tmp_path / ".env").write_text("OPENAI_API_KEY=real\nHARNESS_MAX_TURNS=30\n", encoding="utf-8")
    settings = Settings.load(tmp_path)
    assert settings.max_turns == 30
    assert settings.max_turns_source == ".env"

    monkeypatch.setenv("HARNESS_MAX_TURNS", "40")
    settings = Settings.load(tmp_path)
    assert settings.max_turns == 40
    assert settings.max_turns_source == "process environment"


def test_settings_loads_and_validates_model_allowlist(tmp_path: Path) -> None:
    """The default model must be an exact member of the configured LiteLLM aliases."""
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=real\nOPENAI_MODEL=gpt-5.5\nHARNESS_MODELS=gpt-5.5,gpt-oss:20b\n",
        encoding="utf-8",
    )
    settings = Settings.load(tmp_path)
    assert settings.model == "gpt-5.5"
    assert settings.models == ("gpt-5.5", "gpt-oss:20b")

    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=real\nOPENAI_MODEL=gpt-5.5\nHARNESS_MODELS=gpt-oss:20b\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="included"):
        Settings.load(tmp_path)


@pytest.mark.parametrize(
    "key", ["", "sk-local-your-key", "replace-with-the-key-from-Show-Gateway-Credentials"]
)
def test_settings_rejects_missing_or_placeholder_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """The harness cannot accidentally launch with documented placeholders."""
    monkeypatch.setenv("OPENAI_API_KEY", key)
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)


def test_settings_validates_url_and_positive_integers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid endpoints and execution bounds produce user-facing errors."""
    monkeypatch.setenv("OPENAI_API_KEY", "real")
    monkeypatch.setenv("OPENAI_BASE_URL", "localhost:4000")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:4000/v1")
    monkeypatch.setenv("HARNESS_MAX_TURNS", "zero")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)
    monkeypatch.setenv("HARNESS_MAX_TURNS", "0")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)
    monkeypatch.setenv("HARNESS_MAX_TURNS", "101")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)


def test_settings_validates_new_tool_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Context, batch, and patch bounds reject unusable values."""
    monkeypatch.setenv("OPENAI_API_KEY", "real")
    monkeypatch.setenv("HARNESS_CONTEXT_MAX_CHARS", "0")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)
    monkeypatch.setenv("HARNESS_CONTEXT_MAX_CHARS", "60000")
    monkeypatch.setenv("HARNESS_BATCH_MAX_FILES", "33")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)
    monkeypatch.setenv("HARNESS_BATCH_MAX_FILES", "8")
    monkeypatch.setenv("HARNESS_PATCH_MAX_CHARS", "999")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)
    monkeypatch.setenv("HARNESS_PATCH_MAX_CHARS", "100000")
    monkeypatch.setenv("HARNESS_TOOL_PROFILE", "everything")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)
    monkeypatch.setenv("HARNESS_TOOL_PROFILE", "coding")
    monkeypatch.setenv("HARNESS_TOOL_SCHEMA_LIMIT", "4")
    monkeypatch.setenv("HARNESS_TOOL_ACTIVATION_LIMIT", "5")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)


def test_settings_validates_web_provider_url_and_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Web configuration is local-only and internally consistent."""
    monkeypatch.setenv("OPENAI_API_KEY", "real")
    monkeypatch.setenv("HARNESS_WEB_PROVIDER", "remote")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)
    monkeypatch.setenv("HARNESS_WEB_PROVIDER", "searxng")
    monkeypatch.setenv("SEARXNG_BASE_URL", "https://search.example.com")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("HARNESS_WEB_PAGE_MAX_CHARS", "12000")
    monkeypatch.setenv("HARNESS_WEB_TOTAL_MAX_CHARS", "11000")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)


def test_settings_validates_quota_and_plugin_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Advisory budgets and plugin allowlists reject invalid configuration."""
    monkeypatch.setenv("OPENAI_API_KEY", "real")
    monkeypatch.setenv("HARNESS_SESSION_TOKEN_BUDGET", "-1")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)
    monkeypatch.setenv("HARNESS_SESSION_TOKEN_BUDGET", "1000")
    monkeypatch.setenv("HARNESS_TOKEN_WARNING_PERCENT", "101")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)
    monkeypatch.setenv("HARNESS_TOKEN_WARNING_PERCENT", "80")
    monkeypatch.setenv("HARNESS_ENABLED_PLUGINS", "one,one")
    with pytest.raises(ConfigurationError):
        Settings.load(tmp_path)


def test_settings_validates_workflow_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workflow mode, confidence, and retry bounds reject invalid values."""
    monkeypatch.setenv("OPENAI_API_KEY", "real")
    monkeypatch.setenv("HARNESS_WORKFLOW_MODE", "manual")
    with pytest.raises(ConfigurationError, match="HARNESS_WORKFLOW_MODE"):
        Settings.load(tmp_path)
    monkeypatch.setenv("HARNESS_WORKFLOW_MODE", "auto")
    monkeypatch.setenv("HARNESS_WORKFLOW_CONFIDENCE_MIN", "1.5")
    with pytest.raises(ConfigurationError, match="HARNESS_WORKFLOW_CONFIDENCE_MIN"):
        Settings.load(tmp_path)
    monkeypatch.setenv("HARNESS_WORKFLOW_CONFIDENCE_MIN", "0.60")
    monkeypatch.setenv("HARNESS_WORKFLOW_STAGE_MAX_ATTEMPTS", "0")
    with pytest.raises(ConfigurationError, match="HARNESS_WORKFLOW_STAGE_MAX_ATTEMPTS"):
        Settings.load(tmp_path)


def test_settings_validates_evaluation_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Evaluation booleans, evidence bounds, and comparison sizes are validated."""
    monkeypatch.setenv("OPENAI_API_KEY", "real")
    monkeypatch.setenv("HARNESS_EVALUATION_ENABLED", "sometimes")
    with pytest.raises(ConfigurationError, match="HARNESS_EVALUATION_ENABLED"):
        Settings.load(tmp_path)
    monkeypatch.setenv("HARNESS_EVALUATION_ENABLED", "true")
    monkeypatch.setenv("HARNESS_EVALUATION_MAX_TRACE_CHARS", "999")
    with pytest.raises(ConfigurationError, match="HARNESS_EVALUATION_MAX_TRACE_CHARS"):
        Settings.load(tmp_path)
    monkeypatch.setenv("HARNESS_EVALUATION_MAX_TRACE_CHARS", "30000")
    monkeypatch.setenv("HARNESS_EVALUATION_MIN_COMPARISON_CASES", "1")
    with pytest.raises(ConfigurationError, match="HARNESS_EVALUATION_MIN_COMPARISON_CASES"):
        Settings.load(tmp_path)
