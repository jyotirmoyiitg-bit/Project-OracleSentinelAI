"""Local hashing embeddings by default, and an explicit remote adapter."""

import hashlib
import math
from typing import Protocol

import httpx

from sentinel.config.settings import Settings
from sentinel.knowledge.constants import EMBEDDING_DIMENSIONS, LOCAL_DISCLOSURE
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


def build_embedding_provider(settings: Settings) -> HashingEmbeddingProvider | RemoteEmbeddingProvider:
    """Return the configured provider. Local never fails over to a remote service."""
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
