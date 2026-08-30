"""Domain-specific exception hierarchy."""


class HarnessError(Exception):
    """Base exception for expected harness failures."""


class ConfigurationError(HarnessError):
    """Raised when required runtime configuration is invalid."""


class PolicyViolation(HarnessError):
    """Raised when an operation violates an enforced guardrail."""


class ToolExecutionError(HarnessError):
    """Raised when a tool cannot execute its requested operation."""


class SessionError(HarnessError):
    """Raised when session data cannot be read or validated."""


class ModelError(HarnessError):
    """Raised when the configured model provider cannot complete a turn."""


class ContextLimitError(HarnessError):
    """Raised when essential request context cannot fit the configured budget."""


class SpeechError(HarnessError):
    """Raised when a speech request cannot be validated or synthesized."""


class SpeechBusyError(SpeechError):
    """Raised when the single local speech engine is already active."""


class SpeechValidationError(SpeechError):
    """Raised when speech input violates a deterministic request boundary."""


class SpeechUnavailableError(SpeechError):
    """Raised when the configured speech provider cannot synthesize audio."""


class Audio2FaceError(HarnessError):
    """Base error for local audio-driven facial animation."""


class Audio2FaceBusyError(Audio2FaceError):
    """Raised when the single local animation slot is already occupied."""


class Audio2FaceValidationError(Audio2FaceError):
    """Raised when an animation request violates a closed bound."""


class Audio2FaceUnavailableError(Audio2FaceError):
    """Raised when the configured native animation provider cannot run."""


class SpeechInputError(HarnessError):
    """Raised when local microphone speech cannot be processed safely."""


class SpeechInputBusyError(SpeechInputError):
    """Raised when the single local microphone pipeline is already active."""


class SpeechInputValidationError(SpeechInputError):
    """Raised when microphone input violates a deterministic boundary."""


class SpeechInputUnavailableError(SpeechInputError):
    """Raised when a configured local recognition model cannot run."""


class VoiceConversationError(HarnessError):
    """Raised when the protected model-only conversation workflow fails."""


class VoiceConversationBusyError(VoiceConversationError):
    """Raised when another voice-conversation generation is active."""


class VoiceConversationValidationError(VoiceConversationError):
    """Raised when a voice-conversation request violates a deterministic bound."""


class VoiceConversationNotFoundError(VoiceConversationError):
    """Raised when a requested saved voice conversation does not exist."""


class VoiceConversationStorageError(VoiceConversationError):
    """Raised when protected voice-conversation persistence fails."""


class VoiceAgentProfileError(HarnessError):
    """Base error for configurable voice-agent profiles."""


class VoiceAgentProfileValidationError(VoiceAgentProfileError):
    """Raised when a profile or snapshot violates a bounded policy."""


class VoiceAgentProfileNotFoundError(VoiceAgentProfileError):
    """Raised when a requested profile does not exist."""


class VoiceAgentProfileStorageError(VoiceAgentProfileError):
    """Raised when profile persistence cannot be completed safely."""


class TaskCancelledError(HarnessError):
    """Raised at a safe boundary after browser task cancellation."""
