"""Embedding providers.

  openai   -> OpenAI text-embedding-* models (real vectors)
  voyage   -> Voyage AI voyage-3-large
  fake     -> deterministic, offline word-hash embeddings (dev/demo, no API key)

The fake embedder is a unigram+bigram feature-hash bag-of-words vector, so
cosine similarity behaves like lexical overlap — enough for local smoke tests.
"""
from __future__ import annotations

import hashlib
import math
import os
import re


class Embedder:
    name = "abstract"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    @property
    def dimensions(self) -> int:
        return 0

    def embed_one(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]


class FakeEmbedder(Embedder):
    """Deterministic hashed word-vector embedder — zero API cost, offline."""

    name = "fake"
    dims: int
    _MASK = (1 << 32) - 1
    _STOPWORDS = frozenset(
        "a an and are as at be but by for from has have how i if in is it its of on or so that the this to was"
        " what when where which who why will with you your me my not no yes".split()
    )
    _WORD_RE = re.compile(r"[a-z0-9']+")

    def __init__(self, dims: int = 1536) -> None:
        self.dims = int(dims)

    @property
    def dimensions(self) -> int:
        return self.dims

    def _tokens(self, text: str) -> list[str]:
        return [t for t in self._WORD_RE.findall(text.lower()) if t not in self._STOPWORDS]

    def _token_indices(self, text: str):
        tokens = self._tokens(text)
        for tok in tokens:
            h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8)
            idx = int.from_bytes(h.digest()[:4], "little") % self.dims
            sign = 1.0 if (h.digest()[7] % 2 == 0) else -1.0
            yield idx, sign, 1.0
        for a, b in zip(tokens, tokens[1:]):
            h = hashlib.blake2b(f"{a} {b}".encode("utf-8"), digest_size=8)
            idx = int.from_bytes(h.digest()[:4], "little") % self.dims
            sign = 1.0 if (h.digest()[7] % 2 == 0) else -1.0
            yield idx, sign, 0.6

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        for idx, sign, weight in self._token_indices(text):
            vec[idx] += sign * weight
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class OpenAIEmbedder(Embedder):
    name = "openai"

    def __init__(self, model: str = "text-embedding-3-small", api_key: str | None = None) -> None:
        from openai import OpenAI  # deferred import keeps startup cheap

        self.model = model
        self._client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        if not os.getenv("OPENAI_API_KEY") and not api_key:
            raise RuntimeError("OPENAI_API_KEY missing (set it in .env)")
        self._dims = 1536 if "text-embedding-3-small" in model else 3072

    @property
    def dimensions(self) -> int:
        return self._dims

    def embed_texts(self, texts: list[str], batch: int = 96) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), batch):
            resp = self._client.embeddings.create(model=self.model, input=texts[i : i + batch])
            vectors.extend(d.embedding for d in resp.data)
        return vectors


class VoyageEmbedder(Embedder):
    name = "voyage"

    def __init__(self, model: str = "voyage-3-large", api_key: str | None = None) -> None:
        try:
            import voyageai  # type: ignore # noqa: F401
        except ImportError as exc:
            raise RuntimeError("embedding.provider is 'voyage' but the 'voyageai' package is not installed") from exc
        self.model = model
        self._client = voyageai.Client(api_key=api_key or os.getenv("VOYAGE_API_KEY"))

    @property
    def dimensions(self) -> int:
        return 1024 if "large" in self.model else 512

    def embed_texts(self, texts: list[str], batch: int = 64) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), batch):
            resp = self._client.embed(texts[i : i + batch], model=self.model, input_type="document")
            vectors.extend(list(v) for v in resp.embeddings)
        return vectors


def create_embedder(settings) -> Embedder:
    cfg = settings.embedding
    provider = (cfg.get("provider") or "fake").lower()
    if provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            print("[embedder] OPENAI_API_KEY not set — falling back to the deterministic 'fake' embedder.")
            return FakeEmbedder(dims=settings.embedding_dims)
        return OpenAIEmbedder(model=cfg.get("model", "text-embedding-3-small"))
    if provider == "voyage":
        return VoyageEmbedder(model=cfg.get("voyage_model", "voyage-3-large"))
    if provider == "fake":
        return FakeEmbedder(dims=settings.embedding_dims)
    raise ValueError(f"Unknown embedding.provider: {provider!r} (openai | voyage | fake)")