"""Loopback Ollama embedding adapter."""

from __future__ import annotations

import math
from collections.abc import Sequence

import httpx

from local_harness.domain.errors import ToolExecutionError


class OllamaEmbeddingProvider:
    """Generate normalized embeddings through Ollama's local HTTP API."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: int = 30,
        batch_size: int = 32,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Configure a loopback-only Ollama embedding endpoint."""
        if base_url.rstrip("/") not in {
            "http://127.0.0.1:11434",
            "http://localhost:11434",
            "http://[::1]:11434",
        }:
            raise ValueError("Embedding base URL must be the local Ollama endpoint")
        if not model.strip():
            raise ValueError("Embedding model cannot be empty")
        if not 1 <= batch_size <= 128:
            raise ValueError("Embedding batch size must be between 1 and 128")
        self._base_url = base_url.rstrip("/")
        self._model = model.strip()
        self._timeout = timeout_seconds
        self._batch_size = batch_size
        self._transport = transport

    @property
    def model(self) -> str:
        """Return the configured Ollama model name."""
        return self._model

    def embed(self, values: Sequence[str]) -> list[tuple[float, ...]]:
        """Embed a bounded batch and reject malformed or inconsistent vectors."""
        if not values:
            return []
        output: list[tuple[float, ...]] = []
        try:
            with httpx.Client(
                timeout=self._timeout,
                trust_env=False,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                for start in range(0, len(values), self._batch_size):
                    batch = [item[:8_000] for item in values[start : start + self._batch_size]]
                    response = client.post(
                        f"{self._base_url}/api/embed",
                        json={"model": self._model, "input": batch, "truncate": False},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    vectors = payload.get("embeddings") if isinstance(payload, dict) else None
                    if not isinstance(vectors, list) or len(vectors) != len(batch):
                        raise ToolExecutionError("Ollama returned an invalid embedding count")
                    output.extend(_normalized_vector(item) for item in vectors)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ToolExecutionError(f"Local embeddings unavailable: {exc}") from exc
        dimensions = {len(item) for item in output}
        if len(dimensions) != 1:
            raise ToolExecutionError("Ollama returned inconsistent embedding dimensions")
        return output


def _normalized_vector(value: object) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise ToolExecutionError("Ollama returned an invalid embedding vector")
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        raise ToolExecutionError("Ollama embedding vector contains invalid values")
    numbers = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in numbers):
        raise ToolExecutionError("Ollama embedding vector contains non-finite values")
    norm = math.sqrt(sum(item * item for item in numbers))
    if norm == 0:
        raise ToolExecutionError("Ollama returned a zero embedding vector")
    return tuple(item / norm for item in numbers)
