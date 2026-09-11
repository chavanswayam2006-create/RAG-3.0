"""CLI: ingest syllabus folders into the vector store.

Usage:
    python ingest.py                  # ingest every subject in config.yaml
    python ingest.py --subject math   # one subject
    python ingest.py --force-local    # force the numpy store backend
    python ingest.py --list           # show subjects and current counts
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from app.config import Settings
from app.embedders import create_embedder
from app.ingest import ingest_folder
from app.store import create_store


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest syllabus documents into the StudyBuddy vector store")
    parser.add_argument("--subject", help="only ingest this subject id")
    parser.add_argument("--force-local", action="store_true", help="use the numpy local store backend")
    parser.add_argument("--list", action="store_true", help="show subjects and chunk counts, then exit")
    args = parser.parse_args()

    settings = Settings()
    if args.force_local:
        settings.vector_store["backend"] = "local"

    store = create_store(settings)
    embedder = create_embedder(settings)
    print(f"Vector store backend : {store.backend}")
    print(f"Embedder provider    : {embedder.name} (dims={embedder.dimensions})")

    if args.list:
        print(f"\n{'subject':<14}{'folder':<28}{'documents':>10}{'chunks':>8}")
        for sid, meta in settings.subjects.items():
            folder = Path(meta["path"])
            docs = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".txt", ".md", ".markdown"}] if folder.exists() else []
            print(f"{sid:<14}{str(meta['path']):<28}{len(docs):>10}{store.count(sid):>8}")
        return 0

    which = args.subject or None
    for sid, meta in settings.subjects.items():
        if which and sid != which:
            continue
        folder = Path(meta["path"])
        if not folder.exists():
            print(f"[skip] {sid}: folder missing -> {folder}")
            continue
        print(f"\nIngesting {sid!r} from {folder} …")
        t0 = time.time()
        n_files, n_chunks, chunks = ingest_folder(
            folder,
            subject_id=sid,
            target_tokens=settings.chunk_tokens,
            overlap_ratio=settings.chunk_overlap,
        )
        if not chunks:
            print(f"[warn] {sid}: no parseable documents found")
            continue
        texts = [c for c, _ in chunks]
        metas = [m for _, m in chunks]
        vectors = embedder.embed_texts(texts)
        stored = store.upsert_subject(sid, texts, metas, vectors)
        elapsed = time.time() - t0
        print(f"  parsed {n_files} file(s) -> {n_chunks} chunks -> {stored} embedded+stored in {elapsed:.1f}s")

    print("\nDone. Start the server with:  uvicorn app.main:app --reload --port 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())