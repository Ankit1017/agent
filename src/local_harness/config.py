"""Validated runtime configuration and minimal dotenv loading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from local_harness.domain.errors import ConfigurationError
from local_harness.domain.limits import DEFAULT_MAX_TURNS, validate_max_turns


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings needed to compose the harness."""

    base_url: str
    api_key: str
    model: str
    models: tuple[str, ...] = ()
    max_turns: int = DEFAULT_MAX_TURNS
    max_turns_source: str = "default"
    command_timeout_seconds: int = 120
    max_output_chars: int = 12_000
    context_max_chars: int = 30_000
    batch_max_files: int = 8
    patch_max_chars: int = 100_000
    web_provider: str = "searxng"
    searxng_base_url: str = "http://127.0.0.1:8080"
    web_max_results: int = 8
    web_max_pages: int = 5
    web_page_max_chars: int = 12_000
    web_total_max_chars: int = 30_000
    web_timeout_seconds: int = 15
    session_token_budget: int = 0
    token_warning_percent: int = 80
    enabled_plugins: tuple[str, ...] = ()
    tool_profile: str = "auto"
    tool_schema_limit: int = 8
    tool_activation_limit: int = 5
    lsp_python_command: str = ""
    lsp_typescript_command: str = ""
    project_index_enabled: bool = True
    retrieval_max_files: int = 6
    retrieval_max_chars: int = 12_000
    project_index_max_files: int = 5_000
    project_index_max_chunks: int = 20_000
    embedding_provider: str = "ollama"
    embedding_base_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "embeddinggemma"
    embedding_timeout_seconds: int = 30
    embedding_batch_size: int = 32
    workflow_mode: str = "auto"
    workflow_confidence_min: float = 0.60
    workflow_stage_max_attempts: int = 2
    evaluation_enabled: bool = True
    evaluation_capture_sessions: bool = True
    evaluation_max_trace_chars: int = 30_000
    evaluation_live: bool = False
    evaluation_min_comparison_cases: int = 10
    candidate_proposals_enabled: bool = True
    tts_enabled: bool = False
    tts_default_voice: str = "en_US-lessac-medium"
    tts_voices: tuple[str, ...] = (
        "en_US-lessac-medium",
        "hi_IN-priyamvada-medium",
        "hi_IN-rohan-medium",
    )
    tts_max_chars: int = 5_000
    audio2face_enabled: bool = False
    audio2face_model: str = "mark"
    audio2face_max_seconds: int = 60
    audio2face_timeout_seconds: int = 120
    audio2face_avatar_max_bytes: int = 52_428_800
    audio2face_cuda_root: str = ""
    audio2face_tensorrt_root: str = ""
    stt_enabled: bool = False
    stt_model: str = "small"
    stt_languages: tuple[str, ...] = ("en", "hi")
    stt_wake_phrase: str = "hey buddy"
    stt_max_seconds: int = 15
    stt_silence_ms: int = 800

    @classmethod
    def load(cls, workspace: Path) -> Settings:
        """Load settings from process environment and the workspace ``.env`` file."""
        values = _read_dotenv(workspace / ".env")

        def setting(name: str, default: str = "") -> str:
            return os.environ.get(name, values.get(name, default)).strip()

        api_key = setting("OPENAI_API_KEY")
        if not api_key or api_key in {
            "sk-local-your-key",
            "replace-with-the-key-from-Show-Gateway-Credentials",
        }:
            raise ConfigurationError(
                "OPENAI_API_KEY is missing or still a placeholder. Copy .env.example to .env "
                "and use the key printed by 'Show Gateway Credentials.cmd'."
            )
        base_url = setting("OPENAI_BASE_URL", "http://localhost:4000/v1").rstrip("/")
        model = setting("OPENAI_MODEL", "gpt-oss:20b")
        if not base_url.startswith(("http://", "https://")):
            raise ConfigurationError("OPENAI_BASE_URL must be an HTTP(S) URL")
        if not model:
            raise ConfigurationError("OPENAI_MODEL cannot be empty")
        models = _model_names(setting("HARNESS_MODELS", model))
        if model not in models:
            raise ConfigurationError("OPENAI_MODEL must be included in HARNESS_MODELS")
        process_max_turns = os.environ.get("HARNESS_MAX_TURNS")
        dotenv_max_turns = values.get("HARNESS_MAX_TURNS")
        if process_max_turns is not None:
            raw_max_turns = process_max_turns.strip()
            max_turns_source = "process environment"
        elif dotenv_max_turns is not None:
            raw_max_turns = dotenv_max_turns.strip()
            max_turns_source = ".env"
        else:
            raw_max_turns = str(DEFAULT_MAX_TURNS)
            max_turns_source = "default"
        try:
            max_turns = validate_max_turns(_integer(raw_max_turns, "HARNESS_MAX_TURNS"))
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc
        web_provider = setting("HARNESS_WEB_PROVIDER", "searxng").casefold()
        if web_provider != "searxng":
            raise ConfigurationError("HARNESS_WEB_PROVIDER must be searxng")
        tool_profile = setting("HARNESS_TOOL_PROFILE", "auto").casefold()
        if tool_profile not in {"auto", "coding", "web", "system", "general"}:
            raise ConfigurationError(
                "HARNESS_TOOL_PROFILE must be auto, coding, web, system, or general"
            )
        tool_schema_limit = _bounded_int(
            setting("HARNESS_TOOL_SCHEMA_LIMIT", "8"),
            "HARNESS_TOOL_SCHEMA_LIMIT",
            1,
            32,
        )
        tool_activation_limit = _bounded_int(
            setting("HARNESS_TOOL_ACTIVATION_LIMIT", "5"),
            "HARNESS_TOOL_ACTIVATION_LIMIT",
            1,
            tool_schema_limit,
        )
        workflow_mode = setting("HARNESS_WORKFLOW_MODE", "auto").casefold()
        if workflow_mode not in {"auto", "off"}:
            raise ConfigurationError("HARNESS_WORKFLOW_MODE must be auto or off")
        workflow_confidence = _bounded_float(
            setting("HARNESS_WORKFLOW_CONFIDENCE_MIN", "0.60"),
            "HARNESS_WORKFLOW_CONFIDENCE_MIN",
            0.0,
            1.0,
        )
        tts_voices = _voice_names(
            setting(
                "HARNESS_TTS_VOICES",
                "en_US-lessac-medium,hi_IN-priyamvada-medium,hi_IN-rohan-medium",
            )
        )
        tts_default_voice = setting("HARNESS_TTS_DEFAULT_VOICE", "en_US-lessac-medium")
        if tts_default_voice not in tts_voices:
            raise ConfigurationError(
                "HARNESS_TTS_DEFAULT_VOICE must be included in HARNESS_TTS_VOICES"
            )
        audio2face_model = setting("HARNESS_AUDIO2FACE_MODEL", "mark").casefold()
        if audio2face_model != "mark":
            raise ConfigurationError("HARNESS_AUDIO2FACE_MODEL must be mark")
        stt_model = setting("HARNESS_STT_MODEL", "small").casefold()
        if stt_model != "small":
            raise ConfigurationError("HARNESS_STT_MODEL must be small")
        stt_languages = tuple(
            item.strip().casefold()
            for item in setting("HARNESS_STT_LANGUAGES", "en,hi").split(",")
            if item.strip()
        )
        if (
            not stt_languages
            or len(stt_languages) != len(set(stt_languages))
            or any(item not in {"en", "hi"} for item in stt_languages)
        ):
            raise ConfigurationError("HARNESS_STT_LANGUAGES must contain unique en and/or hi")
        stt_wake_phrase = " ".join(
            setting("HARNESS_STT_WAKE_PHRASE", "hey buddy").casefold().split()
        )
        if stt_wake_phrase != "hey buddy":
            raise ConfigurationError("HARNESS_STT_WAKE_PHRASE must be hey buddy")
        searxng_base_url = setting("SEARXNG_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
        _validate_local_searxng_url(searxng_base_url)
        embedding_provider = setting("HARNESS_EMBEDDING_PROVIDER", "ollama").casefold()
        if embedding_provider != "ollama":
            raise ConfigurationError("HARNESS_EMBEDDING_PROVIDER must be ollama")
        embedding_base_url = setting("HARNESS_EMBEDDING_BASE_URL", "http://127.0.0.1:11434").rstrip(
            "/"
        )
        _validate_loopback_url(embedding_base_url, "HARNESS_EMBEDDING_BASE_URL")
        web_page_max_chars = _bounded_int(
            setting("HARNESS_WEB_PAGE_MAX_CHARS", "12000"),
            "HARNESS_WEB_PAGE_MAX_CHARS",
            1_000,
            100_000,
        )
        web_total_max_chars = _bounded_int(
            setting("HARNESS_WEB_TOTAL_MAX_CHARS", "30000"),
            "HARNESS_WEB_TOTAL_MAX_CHARS",
            1_000,
            200_000,
        )
        if web_total_max_chars < web_page_max_chars:
            raise ConfigurationError(
                "HARNESS_WEB_TOTAL_MAX_CHARS must be at least HARNESS_WEB_PAGE_MAX_CHARS"
            )
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            models=models,
            max_turns=max_turns,
            max_turns_source=max_turns_source,
            command_timeout_seconds=_positive_int(
                setting("HARNESS_COMMAND_TIMEOUT_SECONDS", "120"),
                "HARNESS_COMMAND_TIMEOUT_SECONDS",
            ),
            max_output_chars=_positive_int(
                setting("HARNESS_MAX_OUTPUT_CHARS", "12000"),
                "HARNESS_MAX_OUTPUT_CHARS",
            ),
            context_max_chars=_positive_int(
                setting("HARNESS_CONTEXT_MAX_CHARS", "30000"),
                "HARNESS_CONTEXT_MAX_CHARS",
            ),
            batch_max_files=_bounded_int(
                setting("HARNESS_BATCH_MAX_FILES", "8"),
                "HARNESS_BATCH_MAX_FILES",
                1,
                32,
            ),
            patch_max_chars=_bounded_int(
                setting("HARNESS_PATCH_MAX_CHARS", "100000"),
                "HARNESS_PATCH_MAX_CHARS",
                1_000,
                1_000_000,
            ),
            web_provider=web_provider,
            searxng_base_url=searxng_base_url,
            web_max_results=_bounded_int(
                setting("HARNESS_WEB_MAX_RESULTS", "8"),
                "HARNESS_WEB_MAX_RESULTS",
                1,
                20,
            ),
            web_max_pages=_bounded_int(
                setting("HARNESS_WEB_MAX_PAGES", "5"),
                "HARNESS_WEB_MAX_PAGES",
                1,
                10,
            ),
            web_page_max_chars=web_page_max_chars,
            web_total_max_chars=web_total_max_chars,
            web_timeout_seconds=_bounded_int(
                setting("HARNESS_WEB_TIMEOUT_SECONDS", "15"),
                "HARNESS_WEB_TIMEOUT_SECONDS",
                1,
                120,
            ),
            session_token_budget=_non_negative_int(
                setting("HARNESS_SESSION_TOKEN_BUDGET", "0"),
                "HARNESS_SESSION_TOKEN_BUDGET",
            ),
            token_warning_percent=_bounded_int(
                setting("HARNESS_TOKEN_WARNING_PERCENT", "80"),
                "HARNESS_TOKEN_WARNING_PERCENT",
                1,
                100,
            ),
            enabled_plugins=_plugin_names(setting("HARNESS_ENABLED_PLUGINS", "")),
            tool_profile=tool_profile,
            tool_schema_limit=tool_schema_limit,
            tool_activation_limit=tool_activation_limit,
            lsp_python_command=setting("HARNESS_LSP_PYTHON_COMMAND", ""),
            lsp_typescript_command=setting("HARNESS_LSP_TYPESCRIPT_COMMAND", ""),
            project_index_enabled=_boolean(
                setting("HARNESS_PROJECT_INDEX_ENABLED", "true"),
                "HARNESS_PROJECT_INDEX_ENABLED",
            ),
            retrieval_max_files=_bounded_int(
                setting("HARNESS_RETRIEVAL_MAX_FILES", "6"),
                "HARNESS_RETRIEVAL_MAX_FILES",
                1,
                20,
            ),
            retrieval_max_chars=_bounded_int(
                setting("HARNESS_RETRIEVAL_MAX_CHARS", "12000"),
                "HARNESS_RETRIEVAL_MAX_CHARS",
                1_000,
                100_000,
            ),
            project_index_max_files=_bounded_int(
                setting("HARNESS_PROJECT_INDEX_MAX_FILES", "5000"),
                "HARNESS_PROJECT_INDEX_MAX_FILES",
                1,
                100_000,
            ),
            project_index_max_chunks=_bounded_int(
                setting("HARNESS_PROJECT_INDEX_MAX_CHUNKS", "20000"),
                "HARNESS_PROJECT_INDEX_MAX_CHUNKS",
                1,
                500_000,
            ),
            embedding_provider=embedding_provider,
            embedding_base_url=embedding_base_url,
            embedding_model=setting("HARNESS_EMBEDDING_MODEL", "embeddinggemma"),
            embedding_timeout_seconds=_bounded_int(
                setting("HARNESS_EMBEDDING_TIMEOUT_SECONDS", "30"),
                "HARNESS_EMBEDDING_TIMEOUT_SECONDS",
                1,
                300,
            ),
            embedding_batch_size=_bounded_int(
                setting("HARNESS_EMBEDDING_BATCH_SIZE", "32"),
                "HARNESS_EMBEDDING_BATCH_SIZE",
                1,
                128,
            ),
            workflow_mode=workflow_mode,
            workflow_confidence_min=workflow_confidence,
            workflow_stage_max_attempts=_bounded_int(
                setting("HARNESS_WORKFLOW_STAGE_MAX_ATTEMPTS", "2"),
                "HARNESS_WORKFLOW_STAGE_MAX_ATTEMPTS",
                1,
                5,
            ),
            evaluation_enabled=_boolean(
                setting("HARNESS_EVALUATION_ENABLED", "true"),
                "HARNESS_EVALUATION_ENABLED",
            ),
            evaluation_capture_sessions=_boolean(
                setting("HARNESS_EVALUATION_CAPTURE_SESSIONS", "true"),
                "HARNESS_EVALUATION_CAPTURE_SESSIONS",
            ),
            evaluation_max_trace_chars=_bounded_int(
                setting("HARNESS_EVALUATION_MAX_TRACE_CHARS", "30000"),
                "HARNESS_EVALUATION_MAX_TRACE_CHARS",
                1_000,
                200_000,
            ),
            evaluation_live=_boolean(
                setting("HARNESS_EVALUATION_LIVE", "false"),
                "HARNESS_EVALUATION_LIVE",
            ),
            evaluation_min_comparison_cases=_bounded_int(
                setting("HARNESS_EVALUATION_MIN_COMPARISON_CASES", "10"),
                "HARNESS_EVALUATION_MIN_COMPARISON_CASES",
                2,
                1_000,
            ),
            candidate_proposals_enabled=_boolean(
                setting("HARNESS_CANDIDATE_PROPOSALS_ENABLED", "true"),
                "HARNESS_CANDIDATE_PROPOSALS_ENABLED",
            ),
            tts_enabled=_boolean(setting("HARNESS_TTS_ENABLED", "false"), "HARNESS_TTS_ENABLED"),
            tts_default_voice=tts_default_voice,
            tts_voices=tts_voices,
            tts_max_chars=_bounded_int(
                setting("HARNESS_TTS_MAX_CHARS", "5000"),
                "HARNESS_TTS_MAX_CHARS",
                1,
                5_000,
            ),
            audio2face_enabled=_boolean(
                setting("HARNESS_AUDIO2FACE_ENABLED", "false"),
                "HARNESS_AUDIO2FACE_ENABLED",
            ),
            audio2face_model=audio2face_model,
            audio2face_max_seconds=_bounded_int(
                setting("HARNESS_AUDIO2FACE_MAX_SECONDS", "60"),
                "HARNESS_AUDIO2FACE_MAX_SECONDS",
                1,
                60,
            ),
            audio2face_timeout_seconds=_bounded_int(
                setting("HARNESS_AUDIO2FACE_TIMEOUT_SECONDS", "120"),
                "HARNESS_AUDIO2FACE_TIMEOUT_SECONDS",
                10,
                300,
            ),
            audio2face_avatar_max_bytes=_bounded_int(
                setting("HARNESS_AUDIO2FACE_AVATAR_MAX_BYTES", "52428800"),
                "HARNESS_AUDIO2FACE_AVATAR_MAX_BYTES",
                1_048_576,
                52_428_800,
            ),
            audio2face_cuda_root=setting("CUDA_PATH"),
            audio2face_tensorrt_root=setting("TENSORRT_ROOT_DIR"),
            stt_enabled=_boolean(setting("HARNESS_STT_ENABLED", "false"), "HARNESS_STT_ENABLED"),
            stt_model=stt_model,
            stt_languages=stt_languages,
            stt_wake_phrase=stt_wake_phrase,
            stt_max_seconds=_bounded_int(
                setting("HARNESS_STT_MAX_SECONDS", "15"),
                "HARNESS_STT_MAX_SECONDS",
                1,
                15,
            ),
            stt_silence_ms=_bounded_int(
                setting("HARNESS_STT_SILENCE_MS", "800"),
                "HARNESS_STT_SILENCE_MS",
                300,
                2_000,
            ),
        )


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip("\"'")
    return values


def _integer(raw_value: str, name: str) -> int:
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _positive_int(raw_value: str, name: str) -> int:
    value = _integer(raw_value, name)
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _non_negative_int(raw_value: str, name: str) -> int:
    value = _integer(raw_value, name)
    if value < 0:
        raise ConfigurationError(f"{name} must be zero or greater")
    return value


def _boolean(raw_value: str, name: str) -> bool:
    value = raw_value.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _plugin_names(value: str) -> tuple[str, ...]:
    names = tuple(item.strip() for item in value.split(",") if item.strip())
    if len(names) != len(set(names)):
        raise ConfigurationError("HARNESS_ENABLED_PLUGINS contains duplicate names")
    if any(not item.replace("-", "_").isalnum() for item in names):
        raise ConfigurationError("HARNESS_ENABLED_PLUGINS contains an invalid name")
    return names


def _model_names(value: str) -> tuple[str, ...]:
    """Parse the explicit LiteLLM model alias allowlist."""
    names = tuple(item.strip() for item in value.split(",") if item.strip())
    if not names:
        raise ConfigurationError("HARNESS_MODELS must contain at least one model")
    if len(names) != len(set(names)):
        raise ConfigurationError("HARNESS_MODELS contains duplicate model names")
    allowed = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-")
    if any(
        len(item) > 128 or any(character not in allowed for character in item) for item in names
    ):
        raise ConfigurationError("HARNESS_MODELS contains an invalid model name")
    return names


def _voice_names(value: str) -> tuple[str, ...]:
    """Return unique configured Piper voice identifiers in declaration order."""
    names = tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
    if not names:
        raise ConfigurationError("HARNESS_TTS_VOICES must contain at least one voice")
    if any(len(name) > 128 for name in names):
        raise ConfigurationError("HARNESS_TTS_VOICES contains an identifier that is too long")
    return names


def _bounded_int(raw_value: str, name: str, minimum: int, maximum: int) -> int:
    value = _integer(raw_value, name)
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_float(raw_value: str, name: str, minimum: float, maximum: float) -> float:
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _validate_local_searxng_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ConfigurationError("SEARXNG_BASE_URL is malformed") from exc
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigurationError("SEARXNG_BASE_URL must be a local HTTP URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError("SEARXNG_BASE_URL cannot contain credentials, query, or fragment")


def _validate_loopback_url(value: str, name: str) -> None:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ConfigurationError(f"{name} is malformed") from exc
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigurationError(f"{name} must be a loopback HTTP URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(f"{name} cannot contain credentials, query, or fragment")
