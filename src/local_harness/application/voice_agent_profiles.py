"""Validation and lifecycle use cases for reusable voice-agent profiles."""

from __future__ import annotations

from collections.abc import Callable

from local_harness.application.ports import VoiceAgentProfileRepository
from local_harness.domain.errors import VoiceAgentProfileValidationError
from local_harness.domain.limits import validate_max_turns
from local_harness.domain.voice_agent import VoiceAgentProfile, VoiceAgentProfileSpec
from local_harness.identifiers import new_session_id


class VoiceAgentProfileService:
    """Create revisioned profiles after enforcing exact runtime allowlists."""

    def __init__(
        self,
        repository: VoiceAgentProfileRepository,
        sanitizer: Callable[[str], tuple[str, bool]],
        *,
        workspace_ids: Callable[[], set[str]],
        tool_names: Callable[[str], set[str]],
        models: tuple[str, ...],
        voices: tuple[str, ...],
        global_context_max_chars: int,
    ) -> None:
        """Bind persistence to dynamic workspace/tool availability catalogs."""
        self._repository = repository
        self._sanitizer = sanitizer
        self._workspace_ids = workspace_ids
        self._tool_names = tool_names
        self._models = frozenset(models)
        self._voices = frozenset(voices)
        self._global_context_max_chars = global_context_max_chars

    def create(self, spec: VoiceAgentProfileSpec) -> VoiceAgentProfile:
        """Validate, sanitize, and persist a new revision-one profile."""
        clean = self._validate(spec)
        profile = VoiceAgentProfile(
            new_session_id(),
            1,
            clean.name,
            clean.instructions,
            clean.workspace_id,
            clean.model,
            clean.allowed_tools,
            clean.project_context_enabled,
            clean.workflow_mode,
            clean.max_turns,
            clean.token_budget,
            clean.context_max_chars,
            clean.max_answer_chars,
            clean.tool_schema_limit,
            clean.tool_activation_limit,
            clean.voice_id,
            clean.speaking_rate,
            clean.auto_speak,
        )
        self._repository.save(profile)
        return profile

    def update(self, profile_id: str, spec: VoiceAgentProfileSpec) -> VoiceAgentProfile:
        """Replace editable values and advance the profile revision."""
        clean = self._validate(spec)
        profile = self._repository.load(profile_id)
        for name in VoiceAgentProfileSpec.__dataclass_fields__:
            setattr(profile, name, getattr(clean, name))
        profile.touch()
        self._repository.save(profile)
        return profile

    def clone(self, profile_id: str) -> VoiceAgentProfile:
        """Create a detached copy from one saved profile."""
        source = self._repository.load(profile_id)
        spec = VoiceAgentProfileSpec(
            name=f"{source.name} copy"[:80],
            instructions=source.instructions,
            workspace_id=source.workspace_id,
            model=source.model,
            allowed_tools=source.allowed_tools,
            project_context_enabled=source.project_context_enabled,
            workflow_mode=source.workflow_mode,
            max_turns=source.max_turns,
            token_budget=source.token_budget,
            context_max_chars=source.context_max_chars,
            max_answer_chars=source.max_answer_chars,
            tool_schema_limit=source.tool_schema_limit,
            tool_activation_limit=source.tool_activation_limit,
            voice_id=source.voice_id,
            speaking_rate=source.speaking_rate,
            auto_speak=source.auto_speak,
        )
        return self.create(spec)

    def load(self, profile_id: str) -> VoiceAgentProfile:
        """Load one saved profile."""
        return self._repository.load(profile_id)

    def list_profiles(self) -> list[VoiceAgentProfile]:
        """List saved profiles newest first."""
        return self._repository.list_profiles()

    def delete(self, profile_id: str, confirmation: str) -> None:
        """Delete only after an exact identifier confirmation."""
        if confirmation != profile_id:
            raise VoiceAgentProfileValidationError("Profile deletion confirmation did not match")
        self._repository.delete(profile_id)

    def unavailable_reasons(self, profile: VoiceAgentProfile) -> tuple[str, ...]:
        """Describe missing dependencies without silently changing policy."""
        reasons: list[str] = []
        if profile.workspace_id not in self._workspace_ids():
            return ("Workspace is no longer registered",)
        if profile.model not in self._models:
            reasons.append("Model is no longer configured")
        missing = set(profile.allowed_tools) - self._tool_names(profile.workspace_id)
        if missing:
            reasons.append("Configured tools are unavailable: " + ", ".join(sorted(missing)))
        return tuple(reasons)

    def _validate(self, spec: VoiceAgentProfileSpec) -> VoiceAgentProfileSpec:
        name, _ = self._sanitizer(" ".join(spec.name.split()))
        instructions, _ = self._sanitizer(spec.instructions.strip())
        if not 1 <= len(name) <= 80:
            raise VoiceAgentProfileValidationError("Profile name must contain 1 to 80 characters")
        if len(instructions) > 4_000:
            raise VoiceAgentProfileValidationError("Profile instructions exceed 4000 characters")
        if spec.workspace_id not in self._workspace_ids():
            raise VoiceAgentProfileValidationError("Workspace is not registered")
        if spec.model not in self._models:
            raise VoiceAgentProfileValidationError("Model is not configured")
        if spec.voice_id not in self._voices:
            raise VoiceAgentProfileValidationError("Voice is not configured")
        tools = tuple(dict.fromkeys(spec.allowed_tools))
        if len(tools) != len(spec.allowed_tools) or set(tools) - self._tool_names(
            spec.workspace_id
        ):
            raise VoiceAgentProfileValidationError("Tool allowlist contains unavailable names")
        try:
            validate_max_turns(spec.max_turns)
        except ValueError as exc:
            raise VoiceAgentProfileValidationError(str(exc)) from exc
        if spec.workflow_mode not in {"off", "auto"}:
            raise VoiceAgentProfileValidationError("Workflow mode must be off or auto")
        if not 0 <= spec.token_budget <= 1_000_000:
            raise VoiceAgentProfileValidationError("Token budget must be between 0 and 1000000")
        if not 4_000 <= spec.context_max_chars <= self._global_context_max_chars:
            raise VoiceAgentProfileValidationError("Context limit exceeds its configured bounds")
        if not 500 <= spec.max_answer_chars <= 5_000:
            raise VoiceAgentProfileValidationError("Answer limit must be between 500 and 5000")
        if not 1 <= spec.tool_schema_limit <= 32:
            raise VoiceAgentProfileValidationError("Tool schema limit must be between 1 and 32")
        if not 1 <= spec.tool_activation_limit <= spec.tool_schema_limit:
            raise VoiceAgentProfileValidationError("Tool activation limit must fit schema limit")
        if not 0.75 <= spec.speaking_rate <= 1.5:
            raise VoiceAgentProfileValidationError("Speaking rate must be between 0.75 and 1.50")
        return VoiceAgentProfileSpec(
            name,
            instructions,
            spec.workspace_id,
            spec.model,
            tools,
            spec.project_context_enabled,
            spec.workflow_mode,
            spec.max_turns,
            spec.token_budget,
            spec.context_max_chars,
            spec.max_answer_chars,
            spec.tool_schema_limit,
            spec.tool_activation_limit,
            spec.voice_id,
            spec.speaking_rate,
            spec.auto_speak,
        )
