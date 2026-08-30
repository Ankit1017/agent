"""Protocols defining the harness's external boundaries."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Protocol

from local_harness.domain.audio2face import (
    Audio2FaceStatus,
    FaceAnimation,
    FaceAvatarAsset,
    FaceAvatarChoice,
    FaceAvatarStatus,
)
from local_harness.domain.evaluation import (
    CandidateComparison,
    EvaluationContract,
    EvaluationObservation,
    EvaluationRun,
    HandoffSnapshot,
    HarnessCandidate,
)
from local_harness.domain.maintenance import ArchiveInfo, ExportResult, IntegrityFinding
from local_harness.domain.models import (
    ApprovalDecision,
    CommandExecution,
    Message,
    ModelCompletion,
    ProgressEvent,
    Session,
    ToolDefinition,
    ToolResult,
)
from local_harness.domain.project_memory import (
    DependencyFact,
    IndexDelta,
    ProjectIndexStatus,
    ProjectMemoryQuery,
    RetrievedProjectContext,
)
from local_harness.domain.speech import SpeechRequest, SpeechVoice
from local_harness.domain.speech_input import RecognitionResult
from local_harness.domain.voice_agent import VoiceAgentProfile
from local_harness.domain.voice_conversation import VoiceConversation
from local_harness.domain.web import FetchedWebPage, WebSearchRequest, WebSearchResponse


class ModelClient(Protocol):
    """Complete provider-neutral chat turns."""

    def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelCompletion | Message:
        """Return the model's next assistant message."""


class SpeechSynthesizer(Protocol):
    """Stream speech from one replaceable local provider."""

    def voices(self) -> tuple[SpeechVoice, ...]:
        """Return the configured voice catalog and current load state."""

    def synthesize(self, request: SpeechRequest) -> Iterator[bytes]:
        """Reserve the engine and return a pull-based raw PCM stream."""


class FaceAnimator(Protocol):
    """Generate bounded facial animation from one canonical PCM utterance."""

    def animate(self, pcm_s16le_16khz: bytes) -> FaceAnimation:
        """Return animation frames without retaining the submitted audio."""

    def status(self) -> Audio2FaceStatus:
        """Return a path-free setup and availability report."""


class FaceAvatarRepository(Protocol):
    """Read setup-validated local avatars without accepting runtime paths."""

    def status(self, avatar_id: str | None = None) -> FaceAvatarStatus:
        """Return safe avatar availability and control metadata."""

    def asset(self, avatar_id: str | None = None) -> FaceAvatarAsset:
        """Return one exact validated GLB asset."""

    def catalog(self) -> tuple[FaceAvatarChoice, ...]:
        """Return safe selectable avatar metadata."""

    def default_id(self) -> str:
        """Return the deterministic default avatar identifier."""


class WakeWordStream(Protocol):
    """Consume one local PCM stream and detect its configured wake phrase."""

    def accept(self, chunk: bytes) -> bool:
        """Return whether the chunk completes the configured wake phrase."""

    def reset(self) -> None:
        """Reset decoder state without replacing the loaded model."""

    def close(self) -> None:
        """Release stream-specific recognition resources."""


class WakeWordDetector(Protocol):
    """Create isolated streams over one preloaded local keyword model."""

    def open_stream(self) -> WakeWordStream:
        """Return a new wake-word decoding stream."""


class SpeechRecognizer(Protocol):
    """Transcribe one bounded in-memory local PCM utterance."""

    def transcribe(self, pcm: bytes, languages: tuple[str, ...]) -> RecognitionResult:
        """Return detected text and language without persisting audio."""


class VoiceConversationRepository(Protocol):
    """Persist independent, redacted voice-conversation transcripts."""

    def save(self, conversation: VoiceConversation) -> None:
        """Atomically create or replace one conversation."""

    def load(self, conversation_id: str) -> VoiceConversation:
        """Load one validated conversation."""

    def list_conversations(self) -> list[VoiceConversation]:
        """List validated conversations newest first."""

    def delete(self, conversation_id: str) -> None:
        """Permanently remove one exact conversation document."""


class Tool(Protocol):
    """A model-callable capability."""

    @property
    def definition(self) -> ToolDefinition:
        """Return the model-facing schema for this tool."""

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Execute validated arguments and return bounded text."""


class ApprovalGateway(Protocol):
    """Ask a human whether a terminal command may run."""

    def request(self, command: str, explanation: str, workspace: str) -> ApprovalDecision:
        """Return an explicit approval or rejection with feedback."""


class PatchApprovalGateway(Protocol):
    """Ask a human whether an exact workspace patch may be applied."""

    def request_patch(self, preview: str, explanation: str, workspace: str) -> ApprovalDecision:
        """Return an explicit approval or rejection for a displayed diff."""


class CommandExecutor(Protocol):
    """Run one approved command."""

    def execute(self, command: str) -> CommandExecution:
        """Execute a command and capture its result."""


class SessionRepository(Protocol):
    """Persist and retrieve resumable sessions."""

    def save(self, session: Session) -> None:
        """Persist the complete current session atomically."""

    def load(self, session_id: str) -> Session:
        """Load and validate one session."""

    def list_sessions(self) -> list[Session]:
        """Return known sessions, newest first."""


class SessionExporter(Protocol):
    """Render redacted session exports."""

    def export(self, session: Session, format_name: str) -> ExportResult:
        """Write one uniquely named export."""


class SessionArchiver(Protocol):
    """Archive and restore session documents."""

    def archive(self, session_id: str) -> ArchiveInfo:
        """Create and verify an archive before removing the live document."""

    def list_archives(self) -> list[ArchiveInfo]:
        """Return recoverable archives newest first."""

    def restore(self, session_id: str) -> Session:
        """Validate and restore one archive."""


class SessionIntegrityChecker(Protocol):
    """Inspect and quarantine invalid session artifacts."""

    def scan(self) -> list[IntegrityFinding]:
        """Return current integrity findings without modifying files."""

    def quarantine(self, check_id: str) -> str:
        """Move one unchanged finding to recoverable quarantine."""


class SessionMaintenanceGateway(Protocol):
    """Confirm a recoverable session-maintenance operation."""

    def request_maintenance(self, action: str, details: str) -> ApprovalDecision:
        """Return an explicit default-reject decision."""


class ProgressSink(Protocol):
    """Publish live, user-visible agent lifecycle events."""

    def publish(self, event: ProgressEvent) -> None:
        """Render or forward one progress event."""


class VoiceAgentProfileRepository(Protocol):
    """Persist sanitized reusable voice-agent profiles."""

    def save(self, profile: VoiceAgentProfile) -> None:
        """Atomically create or replace one profile."""

    def load(self, profile_id: str) -> VoiceAgentProfile:
        """Load one profile by exact identifier."""

    def list_profiles(self) -> list[VoiceAgentProfile]:
        """Return saved profiles newest first."""

    def delete(self, profile_id: str) -> None:
        """Permanently remove one exact profile."""


class WebSearchProvider(Protocol):
    """Search the public web through a replaceable provider."""

    def search(self, request: WebSearchRequest) -> WebSearchResponse:
        """Return normalized ranked sources for one validated request."""


class WebPageFetcher(Protocol):
    """Safely retrieve and extract one public web page."""

    def fetch(self, url: str) -> FetchedWebPage:
        """Return bounded extracted content for one public URL."""


class EmbeddingProvider(Protocol):
    """Generate local vector embeddings for bounded text batches."""

    @property
    def model(self) -> str:
        """Return the configured embedding model identifier."""

    def embed(self, values: Sequence[str]) -> list[tuple[float, ...]]:
        """Return one normalized vector per input value."""


class ProjectIndexRepository(Protocol):
    """Persist and query one workspace project-memory index."""

    def status(self) -> ProjectIndexStatus:
        """Return current index status without rebuilding it."""

    def refresh(self, *, rebuild: bool = False) -> ProjectIndexStatus:
        """Create or incrementally refresh the index."""

    def retrieve(self, query: ProjectMemoryQuery) -> RetrievedProjectContext:
        """Return ranked bounded context for a query."""

    def retrieve_for_request(self, prompt: str) -> RetrievedProjectContext:
        """Refresh lazily and retrieve automatic context for a sanitized prompt."""

    def read_symbol(self, symbol_id: str) -> dict[str, object]:
        """Read one current symbol after validating its stored hash."""

    def changed_context(self, limit: int = 50) -> IndexDelta:
        """Return the latest bounded file and symbol delta."""

    def dependencies(self, query: str, limit: int = 50) -> tuple[DependencyFact, ...]:
        """Return matching dependency facts."""

    def mark_dirty(self, paths: Sequence[str]) -> None:
        """Mark paths for refresh at the next safe request boundary."""


class ProjectMemoryRetriever(Protocol):
    """Provide automatic request context without exposing persistence details."""

    def retrieve_for_request(self, prompt: str) -> RetrievedProjectContext:
        """Refresh lazily and retrieve bounded context for a sanitized prompt."""


class EvaluationRepository(Protocol):
    """Persist workspace-local evaluation evidence and candidate proposals."""

    def save_contract(self, contract: EvaluationContract) -> None:
        """Persist or replace one request contract."""

    def get_contract(self, session_id: str, request_number: int) -> EvaluationContract | None:
        """Return one request contract when available."""

    def save_observation(self, observation: EvaluationObservation) -> None:
        """Persist or replace one redacted observation."""

    def get_observation(self, session_id: str, request_number: int) -> EvaluationObservation | None:
        """Return one request observation when available."""

    def list_observations(self, limit: int = 20) -> tuple[EvaluationObservation, ...]:
        """Return recent observations newest first."""

    def mark_observation(
        self, session_id: str, request_number: int, outcome: str, note: str
    ) -> EvaluationObservation:
        """Attach a bounded explicit user outcome to an observation."""

    def save_handoff(self, snapshot: HandoffSnapshot) -> None:
        """Persist the latest request handoff snapshot."""

    def latest_handoff(self, session_id: str) -> HandoffSnapshot | None:
        """Return the latest handoff for a session."""

    def save_run(self, run: EvaluationRun) -> None:
        """Persist an evaluation-suite run."""

    def get_run(self, run_id: str) -> EvaluationRun | None:
        """Return one evaluation run."""

    def save_comparison(self, comparison: CandidateComparison) -> None:
        """Persist one immutable comparison report."""

    def save_candidate(self, candidate: HarnessCandidate) -> None:
        """Persist or update a controlled candidate proposal."""

    def get_candidate(self, candidate_id: str) -> HarnessCandidate | None:
        """Return one candidate proposal."""

    def list_candidates(self, limit: int = 20) -> tuple[HarnessCandidate, ...]:
        """Return recent candidates newest first."""
