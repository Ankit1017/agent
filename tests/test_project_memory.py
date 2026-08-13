"""Tests for persistent local project memory and embedding fallback."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from local_harness.application.agent import AgentService
from local_harness.application.context import ContextBuilder
from local_harness.application.ports import Tool
from local_harness.application.tool_registry import ToolRegistry
from local_harness.domain.errors import HarnessError, ToolExecutionError
from local_harness.domain.models import Message, Session, ToolDefinition, ToolResult
from local_harness.domain.project_memory import (
    DependencyFact,
    IndexDelta,
    IndexedFile,
    ProjectIndexStatus,
    ProjectMemoryQuery,
    RetrievedProjectContext,
)
from local_harness.guardrails.path_policy import WorkspacePathPolicy
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.infrastructure.ollama_embeddings import OllamaEmbeddingProvider
from local_harness.infrastructure.project_index import (
    SqliteProjectMemoryIndex,
    _fallback_symbols,
    _symbols,
)
from local_harness.infrastructure.project_memory_tools import (
    ChangedContextTool,
    DependencyContextTool,
    ProjectMemoryTool,
    ReadSymbolTool,
)


class FakeEmbedding:
    """Return deterministic normalized vectors for offline tests."""

    model = "fake-embedding"

    def embed(self, values: Sequence[str]) -> list[tuple[float, ...]]:
        """Map authentication text to one axis and all other text to another."""
        return [(1.0, 0.0) if "auth" in value.casefold() else (0.0, 1.0) for value in values]


class FailingEmbedding:
    """Simulate an unavailable local embedding model."""

    model = "missing"

    def embed(self, values: Sequence[str]) -> list[tuple[float, ...]]:
        """Raise the safe adapter-level failure."""
        raise ToolExecutionError("embedding model missing")


class MutableEmbedding:
    """Return a caller-controlled vector dimension."""

    model = "mutable"

    def __init__(self) -> None:
        self.dimensions = 2

    def embed(self, values: Sequence[str]) -> list[tuple[float, ...]]:
        """Return normalized single-axis vectors of the selected dimension."""
        vector = (1.0, *([0.0] * (self.dimensions - 1)))
        return [vector for _ in values]


@dataclass
class MemoryRepository:
    """Persist sessions in memory for automatic-context tests."""

    saves: int = 0

    def save(self, session: Session) -> None:
        """Count one save."""
        self.saves += 1

    def load(self, session_id: str) -> Session:
        """Reject unused loading."""
        raise AssertionError

    def list_sessions(self) -> list[Session]:
        """Return no sessions."""
        return []


@dataclass
class FinalModel:
    """Capture one provider context and return a final answer."""

    calls: list[Sequence[Message]] = field(default_factory=list)

    def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> Message:
        """Record context and complete immediately."""
        self.calls.append(messages)
        return Message("assistant", "<step_summary>Answered</step_summary>\nDone.")


class NoopTool:
    """Provide one schema so the test model contract remains realistic."""

    @property
    def definition(self) -> ToolDefinition:
        """Return a closed schema."""
        return ToolDefinition(
            "inspect", "Inspect", {"type": "object", "additionalProperties": False}
        )

    def execute(self, arguments: object) -> ToolResult:
        """Return an unused result."""
        return ToolResult("unused")


class AutomaticMemory:
    """Return fixed ephemeral context and track retrieval count."""

    def __init__(self) -> None:
        self.calls = 0

    def status(self) -> ProjectIndexStatus:
        """Return a ready status."""
        return ProjectIndexStatus(True, generation=3, stale=False)

    def refresh(self, *, rebuild: bool = False) -> ProjectIndexStatus:
        """Return the unchanged fake status."""
        return self.status()

    def retrieve(self, query: ProjectMemoryQuery) -> RetrievedProjectContext:
        """Delegate explicit retrieval to the fixed result."""
        return self.retrieve_for_request(query.text)

    def retrieve_for_request(self, prompt: str) -> RetrievedProjectContext:
        """Count and return fixed ephemeral context."""
        self.calls += 1
        return RetrievedProjectContext(
            "architecture",
            (),
            "<project_memory>relevant authentication module</project_memory>",
            "lexical",
            3,
            2000,
            64,
        )

    def read_symbol(self, symbol_id: str) -> dict[str, object]:
        """Return an unused symbol result."""
        return {}

    def changed_context(self, limit: int = 50) -> IndexDelta:
        """Return an empty delta."""
        return IndexDelta(3)

    def dependencies(self, query: str, limit: int = 50) -> tuple[DependencyFact, ...]:
        """Return no dependency facts."""
        return ()

    def mark_dirty(self, paths: Sequence[str]) -> None:
        """Ignore dirty paths."""
        return None


def _index(
    workspace: Path, embedding: FakeEmbedding | FailingEmbedding
) -> SqliteProjectMemoryIndex:
    return SqliteProjectMemoryIndex(
        WorkspacePathPolicy(workspace),
        embedding,
        SecretRedactor(("super-secret-value",)),
    )


def test_index_refresh_retrieval_symbols_dependencies_and_delta(tmp_path: Path) -> None:
    """A generation stores compact facts and refreshes only changed workspace content."""
    (tmp_path / "app.py").write_text(
        "def authenticate(user: str) -> bool:\n    return bool(user)\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["httpx>=0.28"]\n', encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Service\nAuthentication design.\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=super-secret-value", encoding="utf-8")
    index = _index(tmp_path, FakeEmbedding())

    status = index.refresh()
    result = index.retrieve(ProjectMemoryQuery("authenticate function", category="symbol"))

    assert status.generation == 1
    assert status.files == 3
    assert status.symbols >= 1
    assert status.dependencies == 1
    assert result.retrieval_mode == "semantic"
    assert result.hits[0].path == "app.py"
    assert "super-secret-value" not in result.rendered
    symbol = index.read_symbol(result.hits[0].source_id)
    assert "authenticate" in str(symbol["content"])
    assert index.dependencies("httpx")[0].constraint == ">=0.28"
    assert "app.py" in index.changed_context().created

    (tmp_path / "app.py").write_text(
        "def authenticate() -> bool:\n    return True\n", encoding="utf-8"
    )
    second = index.refresh()
    assert second.generation == 2
    assert "app.py" in index.changed_context().modified


def test_index_uses_lexical_fallback_and_skips_unsafe_content(tmp_path: Path) -> None:
    """Missing embeddings do not block retrieval and protected/binary files stay absent."""
    (tmp_path / "service.py").write_text("def unique_handler():\n    pass\n", encoding="utf-8")
    (tmp_path / "binary.py").write_bytes(b"x\x00y")
    protected = tmp_path / ".harness"
    protected.mkdir()
    (protected / "secret.py").write_text("secret", encoding="utf-8")
    index = _index(tmp_path, FailingEmbedding())

    status = index.refresh()
    result = index.retrieve(ProjectMemoryQuery("unique_handler"))

    assert status.retrieval_mode == "lexical"
    assert result.retrieval_mode == "lexical"
    assert result.hits[0].path == "service.py"
    assert status.files == 1


def test_index_parses_supported_manifests_and_rename_delta(tmp_path: Path) -> None:
    """Python, Node, Rust, Go, Java, and .NET dependency facts remain execution-free."""
    manifests = {
        "requirements.txt": "requests>=2\n# ignored\n-r other.txt\n",
        "package.json": json.dumps(
            {"dependencies": {"react": "19"}, "devDependencies": {"vite": "8"}}
        ),
        "Cargo.toml": '[dependencies]\nserde = "1"\nregex = { version = "2" }\n',
        "go.mod": "module example.com/app\nrequire example.com/lib v1.2.3\n",
        "pom.xml": (
            "<project><dependencies><dependency><groupId>org.demo</groupId>"
            "<artifactId>core</artifactId><version>1</version></dependency></dependencies></project>"
        ),
        "sample.csproj": '<Project><ItemGroup><PackageReference Include="NUnit" Version="4" />'
        "</ItemGroup></Project>",
    }
    for name, content in manifests.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    old = tmp_path / "old.py"
    old.write_text("def moved():\n    return 1\n", encoding="utf-8")
    index = _index(tmp_path, FakeEmbedding())
    index.refresh()

    facts = index.dependencies("")
    assert {item.ecosystem for item in facts} == {
        "python",
        "node",
        "rust",
        "go",
        "java",
        "dotnet",
    }
    old.rename(tmp_path / "new.py")
    index.refresh()
    assert index.changed_context().renamed == ("old.py -> new.py",)


def test_index_rejects_stale_symbols_limits_and_invalid_queries(tmp_path: Path) -> None:
    """Stable IDs, public bounds, and stale-hash checks fail closed."""
    source = tmp_path / "app.py"
    source.write_text("def handler():\n    return True\n", encoding="utf-8")
    index = _index(tmp_path, FakeEmbedding())
    index.refresh()
    symbol_id = index.retrieve(ProjectMemoryQuery("handler", category="symbol")).hits[0].source_id
    source.write_text("def handler():\n    return False\n", encoding="utf-8")

    with pytest.raises(ToolExecutionError, match="changed"):
        index.read_symbol(symbol_id)
    with pytest.raises(ToolExecutionError, match="Unknown"):
        index.read_symbol("unknown")
    with pytest.raises(ToolExecutionError, match="1-500"):
        index.retrieve(ProjectMemoryQuery(""))
    with pytest.raises(ToolExecutionError, match="between 1 and 12"):
        index.retrieve(ProjectMemoryQuery("x", max_results=13))
    with pytest.raises(ToolExecutionError, match="Changed-context"):
        index.changed_context(0)
    with pytest.raises(ToolExecutionError, match="Dependency"):
        index.dependencies("", 101)
    with pytest.raises(HarnessError, match="protected"):
        index.mark_dirty([".env"])


def test_index_reembeds_on_dimension_change_and_recovers_corruption(tmp_path: Path) -> None:
    """Vector dimension changes re-embed the cache and corrupt databases are quarantined."""
    (tmp_path / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
    embedding = MutableEmbedding()
    index = SqliteProjectMemoryIndex(WorkspacePathPolicy(tmp_path), embedding, SecretRedactor())
    assert index.refresh().embedding_dimensions == 2
    embedding.dimensions = 3
    index.mark_dirty(["main.py"])
    assert index.refresh().embedding_dimensions == 3

    other = tmp_path / "other"
    other.mkdir()
    cache = other / ".harness" / "cache" / "project-memory"
    cache.mkdir(parents=True)
    (cache / "index.sqlite3").write_text("not sqlite", encoding="utf-8")
    recovered = _index(other, FakeEmbedding())
    assert recovered.status().generation == 0
    assert list(cache.glob("corrupt-*.sqlite3"))


def test_symbol_fallback_covers_supported_declaration_styles() -> None:
    """Offline grammar fallback extracts conservative symbols across supported languages."""
    samples = {
        "python": "def run():\n    return True\nclass Service:\n    pass\n",
        "javascript": "function run() { return true; }\nconst handler = () => {};",
        "typescript": "interface Service {}\nexport const handler = () => {};",
        "tsx": "type Props = {};\nfunction View() { return null; }",
        "go": "func Run() {}\ntype Service struct {}",
        "rust": "pub fn run() {}\nstruct Service {}",
        "java": "public class Service {\n}",
        "csharp": "public sealed class Service {\n}",
        "bash": "function run() { true; }\nother() { true; }",
        "powershell": "function Invoke-Run { }\nclass Service { }",
    }
    suffixes = {
        "python": ".py",
        "javascript": ".js",
        "typescript": ".ts",
        "tsx": ".tsx",
        "go": ".go",
        "rust": ".rs",
        "java": ".java",
        "csharp": ".cs",
        "bash": ".sh",
        "powershell": ".ps1",
    }
    names: set[str] = set()
    for language, text in samples.items():
        item = IndexedFile(
            f"sample{suffixes[language]}", "symbol", language, len(text), 1, "hash", "summary"
        )
        names.update(value.name for value in _fallback_symbols(item, text, language))
    python_item = IndexedFile("sample.py", "symbol", "python", 1, 1, "hash", "summary")

    assert {"run", "Service", "handler", "Run", "View", "Invoke-Run"} <= names
    assert _fallback_symbols(python_item, "value = 1", "unknown") == []
    assert _symbols(python_item, "def cached():\n    pass", {"python"})[0].name == "cached"


def test_memory_tools_return_versioned_bounded_envelopes(tmp_path: Path) -> None:
    """All four read-only tools expose closed schemas and structured JSON output."""
    (tmp_path / "main.py").write_text("class Runner:\n    pass\n", encoding="utf-8")
    index = _index(tmp_path, FakeEmbedding())
    index.refresh()
    redactor = SecretRedactor()
    tools: list[Tool] = [
        ProjectMemoryTool(index, redactor, max_output_chars=12_000),
        ChangedContextTool(index, redactor, max_output_chars=12_000),
        DependencyContextTool(index, redactor, max_output_chars=12_000),
    ]
    calls: tuple[dict[str, object], ...] = ({"query": "Runner"}, {}, {})
    for tool, arguments in zip(tools, calls, strict=True):
        assert tool.definition.parameters["additionalProperties"] is False
        payload = json.loads(tool.execute(arguments).content)
        assert payload["version"] == 1
    hit = index.retrieve(ProjectMemoryQuery("Runner", category="symbol")).hits[0]
    symbol = ReadSymbolTool(index, redactor, max_output_chars=12_000).execute(
        {"symbol_id": hit.source_id}
    )
    assert symbol.is_error is False


def test_memory_tools_translate_invalid_arguments(tmp_path: Path) -> None:
    """Malformed model arguments become safe tool errors without exceptions."""
    (tmp_path / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")
    index = _index(tmp_path, FakeEmbedding())
    index.refresh()
    redactor = SecretRedactor()
    project = ProjectMemoryTool(index, redactor, max_output_chars=1_000)
    symbol = ReadSymbolTool(index, redactor, max_output_chars=1_000)
    changed = ChangedContextTool(index, redactor, max_output_chars=1_000)
    dependencies = DependencyContextTool(index, redactor, max_output_chars=1_000)

    assert project.execute({"query": 3}).is_error
    assert project.execute({"query": "x", "category": "wrong"}).is_error
    assert project.execute({"query": "x", "max_results": True}).is_error
    assert symbol.execute({"symbol_id": "missing"}).is_error
    assert changed.execute({"limit": 0}).is_error
    assert dependencies.execute({"limit": "many"}).is_error


def test_ollama_adapter_batches_and_normalizes() -> None:
    """The direct loopback adapter uses batch /api/embed and normalizes vectors."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        values = json.loads(request.content)["input"]
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0] for _ in values]})

    provider = OllamaEmbeddingProvider(
        "http://127.0.0.1:11434",
        "embeddinggemma",
        batch_size=2,
        transport=httpx.MockTransport(handler),
    )
    vectors = provider.embed(["one", "two", "three"])

    assert len(requests) == 2
    assert vectors == [(0.6, 0.8), (0.6, 0.8), (0.6, 0.8)]
    assert requests[0].url.path == "/api/embed"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"embeddings": []},
        {"embeddings": [[]]},
        {"embeddings": [[True, 1]]},
        {"embeddings": [[float("inf"), 1]]},
        {"embeddings": [[0, 0]]},
    ],
)
def test_ollama_adapter_rejects_malformed_vectors(payload: object) -> None:
    """Malformed local responses never enter the persistent vector index."""
    provider = OllamaEmbeddingProvider(
        "http://localhost:11434",
        "embeddinggemma",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    with pytest.raises(ToolExecutionError):
        provider.embed(["value"])


def test_ollama_adapter_validates_model_batch_and_http_failures() -> None:
    """Configuration and transport failures are translated safely."""
    with pytest.raises(ValueError, match="cannot be empty"):
        OllamaEmbeddingProvider("http://127.0.0.1:11434", "")
    with pytest.raises(ValueError, match="between 1 and 128"):
        OllamaEmbeddingProvider("http://127.0.0.1:11434", "model", batch_size=0)
    provider = OllamaEmbeddingProvider(
        "http://[::1]:11434",
        "model",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    with pytest.raises(ToolExecutionError, match="unavailable"):
        provider.embed(["value"])
    assert provider.embed([]) == []


def test_context_shrinks_ephemeral_memory_before_current_prompt() -> None:
    """Project memory is not persisted and yields before essential current-request content."""
    prompt = Message("user", "keep-current-prompt", request_number=1)
    result = ContextBuilder(900).build(
        "system",
        [prompt],
        [ToolDefinition("x", "x", {"type": "object"})],
        1,
        "memory " + "x" * 5_000,
    )

    assert result[-1].content == "keep-current-prompt"
    assert prompt.content == "keep-current-prompt"


def test_agent_injects_memory_once_without_persisting_it() -> None:
    """Automatic retrieval is ephemeral and creates an observable audit event."""
    memory = AutomaticMemory()
    model = FinalModel()
    session = Session("a" * 32, "C:\\workspace", "model")
    service = AgentService(
        model_client=model,
        registry=ToolRegistry([NoopTool()]),
        sessions=MemoryRepository(),
        session=session,
        system_prompt="system",
        max_turns=2,
        project_memory=memory,
    )

    assert service.submit("Fix authentication") == "Done."
    assert memory.calls == 1
    assert any("project_memory" in (item.content or "") for item in model.calls[0])
    assert all("project_memory" not in (item.content or "") for item in session.messages)
    assert any(event.kind == "memory_retrieval" for event in session.events)


@pytest.mark.parametrize("url", ["https://example.com", "http://10.0.0.1:11434"])
def test_ollama_adapter_rejects_non_loopback_urls(url: str) -> None:
    """Embedding transport remains pinned to the local Ollama service."""
    with pytest.raises(ValueError, match="local Ollama"):
        OllamaEmbeddingProvider(url, "embeddinggemma")
