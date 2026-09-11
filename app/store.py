"""Vector store abstraction — subject-scoped retrieval.

Backends:
  chroma -> ChromaDB PersistentClient, collection filtered by `where=subject_id`
  local  -> dependency-free numpy brute-force store persisted to .store/local/

The `local` backend swaps in automatically if `chromadb` cannot be imported on
the active Python version. For production scale, swap in Pinecone/Qdrant/Weaviate
behind the same interface.
"""
from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np  # type: ignore


@dataclass
class SearchHit:
    id: str
    text: str
    metadata: dict
    score: float  # cosine similarity in 0..1


class VectorStore:
    backend = "abstract"

    def upsert_subject(self, subject_id: str, texts: list[str], metadatas: list[dict], vectors: list[list[float]]) -> int:
        raise NotImplementedError

    def delete_subject(self, subject_id: str) -> None:
        raise NotImplementedError

    def search(self, subject_id: str, vector: list[float], top_k: int) -> list[SearchHit]:
        raise NotImplementedError

    def count(self, subject_id: str) -> int:
        raise NotImplementedError

    def list_subjects(self) -> set[str]:
        raise NotImplementedError


# --------------------------------------------------------------------------
# Chroma backend
# --------------------------------------------------------------------------
class ChromaStore(VectorStore):
    backend = "chroma"

    def __init__(self, path: str, collection: str, dims: int) -> None:
        import chromadb

        self._dims = dims
        self._client = chromadb.PersistentClient(path=path)
        kw = {"name": collection, "metadata": {"hnsw:space": "cosine"}}
        try:
            self._col = self._client.get_or_create_collection(**kw)
        except TypeError:  # older chromadb
            self._col = self._client.get_or_create_collection(kw["name"], metadata=kw["metadata"])

    def upsert_subject(self, subject_id: str, texts: list[str], metadatas: list[dict], vectors: list[list[float]]) -> int:
        self.delete_subject(subject_id)
        ids = [f"{subject_id}::{i}" for i in range(len(texts))]
        if not texts:
            return 0
        self._col.upsert(ids=ids, documents=texts, metadatas=metadatas, embeddings=vectors)
        return len(texts)

    def delete_subject(self, subject_id: str) -> None:
        self._col.delete(where={"subject_id": subject_id})

    def search(self, subject_id: str, vector: list[float], top_k: int) -> list[SearchHit]:
        res = self._col.query(
            query_embeddings=[vector],
            n_results=max(1, top_k),
            where={"subject_id": subject_id},
            include=["documents", "metadatas", "distances"],
        )
        # Chroma returns lists-of-lists (one entry per query embedding).
        ids = (res.get("ids") or [[]])[0] or []
        documents = (res.get("documents") or [[]])[0] or []
        metadatas = (res.get("metadatas") or [[]])[0] or []
        distances = (res.get("distances") or [[]])[0] or []

        hits: list[SearchHit] = []
        for i, text in enumerate(documents):
            dist = float(distances[i]) if i < len(distances) else 1.0
            meta = dict(metadatas[i]) if i < len(metadatas) and isinstance(metadatas[i], dict) else {}
            hits.append(
                SearchHit(
                    id=ids[i] if i < len(ids) else f"{subject_id}::{i}",
                    text=text,
                    metadata=meta,
                    score=max(0.0, min(1.0, 1.0 - dist)),
                )
            )
        return hits

    def count(self, subject_id: str) -> int:
        res = self._col.get(where={"subject_id": subject_id})
        return len(res.get("ids", []))

    def list_subjects(self) -> set[str]:
        metas = self._col.get(include=["metadatas"]).get("metadatas", []) or []
        return {m.get("subject_id") for m in metas if m.get("subject_id")}


# --------------------------------------------------------------------------
# numpy local backend (dependency-free fallback + dev default option)
# --------------------------------------------------------------------------
class LocalStore(VectorStore):
    backend = "local"

    def __init__(self, path: str, dims: int) -> None:
        self._dims = dims
        self._root = Path(path) / "local"
        self._root.mkdir(parents=True, exist_ok=True)

    def _npy(self, sid: str) -> Path:
        return self._root / f"{sid}.npy"

    def _json(self, sid: str) -> Path:
        return self._root / f"{sid}.json"

    @staticmethod
    def _norm(vector) -> np.ndarray:
        v = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(v)) or 1.0
        return (v / norm).astype(np.float32)

    def upsert_subject(self, subject_id: str, texts: list[str], metadatas: list[dict], vectors: list[list[float]]) -> int:
        if not texts:
            return 0
        arr = np.asarray(vectors, dtype=np.float32)
        np.save(self._npy(subject_id), arr)
        payload = {"texts": texts, "metadatas": metadatas}
        self._json(subject_id).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return len(texts)

    def delete_subject(self, subject_id: str) -> None:
        self._npy(subject_id).unlink(missing_ok=True)
        self._json(subject_id).unlink(missing_ok=True)

    def _load(self, subject_id: str) -> tuple[np.ndarray, list[str], list[dict], list[str]]:
        npy, js = self._npy(subject_id), self._json(subject_id)
        if not npy.exists() or not js.exists():
            return np.zeros((0, self._dims), dtype=np.float32), [], [], []
        payload = json.loads(js.read_text(encoding="utf-8"))
        arr = np.load(npy)
        ids = [f"{subject_id}::{i}" for i in range(len(payload["texts"]))]
        return arr, payload["texts"], payload["metadatas"], ids

    def search(self, subject_id: str, vector: list[float], top_k: int) -> list[SearchHit]:
        arr, texts, metas, ids = self._load(subject_id)
        if len(arr) == 0:
            return []
        q = self._norm(vector)
        sims = arr.dot(q)
        k = min(max(1, top_k), len(sims))
        idx = np.argsort(sims)[::-1][:k]
        return [SearchHit(id=ids[i], text=texts[i], metadata=dict(metas[i]), score=float(sims[i])) for i in idx]

    def count(self, subject_id: str) -> int:
        arr, *_ = self._load(subject_id)
        return len(arr)

    def list_subjects(self) -> set[str]:
        return {p.stem for p in self._root.glob("*.json")}


def create_store(settings) -> VectorStore:
    cfg = settings.vector_store
    path = str(cfg.get("path", ".store"))
    collection = str(cfg.get("collection", "syllabus_chunks"))
    dims = settings.embedding_dims
    backend = (cfg.get("backend") or "chroma").lower()

    if backend == "chroma":
        try:
            import chromadb  # noqa: F401

            return ChromaStore(path, collection, dims)
        except Exception as exc:  # pragma: no cover - import/version failures
            warnings.warn(f"chromadb unavailable ({exc}) — falling back to the numpy 'local' store.", RuntimeWarning)
    return LocalStore(path, dims)