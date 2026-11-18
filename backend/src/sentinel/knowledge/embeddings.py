"""Local hashing embeddings by default, a local embedding model through Ollama, and an explicit remote adapter."""

import hashlib
import math
from typing import Protocol

import httpx

from sentinel.config.settings import Settings
from sentinel.knowledge.constants import EMBEDDING_DIMENSIONS, LOCAL_DISCLOSURE, LOCAL_MODEL_DISCLOSURE
from sentinel.knowledge.text import features
from sentinel.knowledge.types import EmbeddingError


class EmbeddingProvider(Protocol):
    """Embedding contract. Chat inference is a separate provider and is not used here."""

    disclosure: str
    model: str
    provider_name: str

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...

    def embed_documents_sync(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query_sync(self, text: str) -> list[float]: ...


def _checked_vector(values: object, dimensions: int) -> list[float]:
    if not isinstance(values, list) or len(values) != dimensions:
        raise EmbeddingError(f"Expected {dimensions} embedding dimensions")
    vector: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EmbeddingError("Embedding values must be numbers")
        number = float(value)
        if not math.isfinite(number):
            raise EmbeddingError("Embedding values must be finite")
        vector.append(number)
    return vector


class HashingEmbeddingProvider:
    """Deterministic local feature hashing. No document text leaves the process."""

    provider_name = "local"

    def __init__(self, dimensions: int, model: str) -> None:
        if dimensions != EMBEDDING_DIMENSIONS:
            raise EmbeddingError(f"Local embeddings must use {EMBEDDING_DIMENSIONS} dimensions")
        self.dimensions = dimensions
        self.model = model
        self.disclosure = LOCAL_DISCLOSURE

    def embed_documents_sync(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query_sync(text) for text in texts]

    def embed_query_sync(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for feature in features(text):
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents_sync(texts)

    async def embed_query(self, text: str) -> list[float]:
        return self.embed_query_sync(text)


# Some embedding models expect a task prefix. The first is for a stored passage, the second for a question.
_PREFIXES: dict[str, tuple[str, str]] = {
    "nomic-embed-text": ("search_document: ", "search_query: "),
    "mxbai-embed-large": ("", "Represent this sentence for searching relevant passages: "),
}
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    return vector if norm == 0 else [value / norm for value in vector]


class OllamaEmbeddingProvider:
    """An embedding model served by Ollama on this computer. Document text does not leave the machine."""

    provider_name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float,
        batch_size: int = 32,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        host = (httpx.URL(base_url).host or "").lower()
        if host not in _LOOPBACK:
            raise EmbeddingError("The embedding model must run on this computer")
        if not model.strip():
            raise EmbeddingError("An embedding model name is required")
        self.url = base_url.rstrip("/") + "/api/embed"
        self.model = model.strip()
        self.timeout = timeout
        self.batch_size = max(1, batch_size)
        self.transport = transport
        self.disclosure = LOCAL_MODEL_DISCLOSURE
        self.dimensions = EMBEDDING_DIMENSIONS
        family = self.model.split(":", 1)[0].rsplit("/", 1)[-1]
        self._document_prefix, self._query_prefix = _PREFIXES.get(family, ("", ""))

    def embed_documents_sync(self, texts: list[str]) -> list[list[float]]:
        return self._embed([self._document_prefix + text for text in texts])

    def embed_query_sync(self, text: str) -> list[float]:
        return self._embed([self._query_prefix + text])[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents_sync(texts)

    async def embed_query(self, text: str) -> list[float]:
        return self.embed_query_sync(text)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        if not texts:
            return vectors
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout, trust_env=False) as client:
                for start in range(0, len(texts), self.batch_size):
                    batch = texts[start:start + self.batch_size]
                    response = client.post(self.url, json={"model": self.model, "input": batch, "truncate": True})
                    if response.status_code == 404:
                        raise EmbeddingError(
                            f"The embedding model {self.model} is not installed. Run: ollama pull {self.model}",
                        )
                    response.raise_for_status()
                    vectors.extend(self._parse(response.json(), len(batch)))
        except httpx.HTTPError as exc:
            raise EmbeddingError("The local embedding model did not answer. Check that Ollama is running.") from exc
        return vectors

    def _parse(self, payload: object, expected: int) -> list[list[float]]:
        raw = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(raw, list) or len(raw) != expected:
            raise EmbeddingError("The embedding count did not match the passages")
        parsed: list[list[float]] = []
        for item in raw:
            if not isinstance(item, list) or not item:
                raise EmbeddingError("The embedding model returned an empty vector")
            if len(item) != self.dimensions:
                raise EmbeddingError(
                    f"{self.model} returns {len(item)} numbers per passage and this installation stores "
                    f"{self.dimensions}. Use nomic-embed-text or another {self.dimensions}-number model.",
                )
            parsed.append(_unit(_checked_vector(item, self.dimensions)))
        return parsed


class RemoteEmbeddingProvider:
    """Remote embeddings run only after an explicit approval and privacy disclosure."""

    provider_name = "remote"

    def __init__(
        self,
        url: str,
        disclosure: str,
        dimensions: int,
        model: str,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not url or not disclosure.strip():
            raise EmbeddingError("Remote embeddings require an approved URL and a privacy disclosure")
        if dimensions != EMBEDDING_DIMENSIONS:
            raise EmbeddingError(f"Remote embeddings must use {EMBEDDING_DIMENSIONS} dimensions")
        self.url = url
        self.disclosure = disclosure.strip()
        self.dimensions = dimensions
        self.model = model
        self.timeout = timeout
        self.transport = transport

    def embed_documents_sync(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
                response = client.post(self.url, json={"texts": texts, "model": self.model})
                response.raise_for_status()
                payload: object = response.json()
        except httpx.HTTPError as exc:
            raise EmbeddingError("Remote embedding request failed") from exc
        return self._parse(payload, len(texts))

    def embed_query_sync(self, text: str) -> list[float]:
        return self.embed_documents_sync([text])[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.url, json={"texts": texts, "model": self.model})
                response.raise_for_status()
                payload: object = response.json()
        except httpx.HTTPError as exc:
            raise EmbeddingError("Remote embedding request failed") from exc
        return self._parse(payload, len(texts))

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_documents([text])
        return vectors[0]

    def _parse(self, payload: object, expected: int) -> list[list[float]]:
        if not isinstance(payload, dict):
            raise EmbeddingError("Remote embedding response did not include embeddings")
        raw = payload.get("embeddings")
        if not isinstance(raw, list) or len(raw) != expected:
            raise EmbeddingError("Remote embedding count did not match the input")
        return [_checked_vector(item, self.dimensions) for item in raw]


def build_embedding_provider(
    settings: Settings,
) -> HashingEmbeddingProvider | OllamaEmbeddingProvider | RemoteEmbeddingProvider:
    """Return the configured provider. Local never fails over to a remote service."""
    if settings.embedding_provider == "ollama":
        return OllamaEmbeddingProvider(
            settings.embedding_local_url,
            settings.embedding_ollama_model,
            settings.embedding_timeout_seconds,
            settings.embedding_batch_size,
        )
    if settings.embedding_provider == "remote":
        if not settings.embedding_remote_approved or not settings.embedding_remote_privacy_disclosure.strip():
            raise EmbeddingError(
                "Remote embeddings are blocked until explicitly approved with a privacy disclosure",
            )
        if not settings.embedding_remote_url:
            raise EmbeddingError("Remote embeddings require embedding_remote_url")
        return RemoteEmbeddingProvider(
            settings.embedding_remote_url,
            settings.embedding_remote_privacy_disclosure,
            settings.embedding_dimensions,
            settings.embedding_model,
            settings.knowledge_parser_timeout_seconds,
        )
    if settings.embedding_provider != "local":
        raise EmbeddingError("Unsupported embedding provider")
    return HashingEmbeddingProvider(settings.embedding_dimensions, settings.embedding_model)
