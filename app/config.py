"""Central settings loaded from config.yaml + .env."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"


class Settings:
    """Typed-ish accessor over the YAML config (kept dependency-free)."""

    def __init__(self, path: Path | str = DEFAULT_CONFIG) -> None:
        load_dotenv(ROOT / ".env")  # no-op if missing
        self.raw_path = Path(path)
        with open(self.raw_path, "r", encoding="utf-8") as fh:
            self.raw: dict[str, Any] = yaml.safe_load(fh) or {}
        self.subjects: dict[str, dict[str, str]] = self.raw.get("subjects", {})

        # Resolve subject data folders relative to the project root.
        for sid, meta in self.subjects.items():
            meta["path"] = str((ROOT / meta["path"]).resolve())
            meta["id"] = sid

        self.embedding: dict[str, Any] = self.raw.get("embedding", {})
        self.llm: dict[str, Any] = self.raw.get("llm", {})
        self.retrieval: dict[str, Any] = self.raw.get("retrieval", {})
        self.vector_store: dict[str, Any] = self.raw.get("vector_store", {})
        self.sessions: dict[str, Any] = self.raw.get("sessions", {})

        self.sessions_dir = ROOT / self.sessions.get("path", "data/sessions")

    def subject_folders(self) -> dict[str, Path]:
        return {sid: Path(meta["path"]) for sid, meta in self.subjects.items()}

    def data_dir(self) -> Path:
        return ROOT / "data"

    # -- convenience accessors ---------------------------------------------
    @property
    def chunk_tokens(self) -> int:
        return int(self.retrieval.get("chunk_tokens", 650))

    @property
    def chunk_overlap(self) -> float:
        return float(self.retrieval.get("chunk_overlap", 0.15))

    @property
    def top_k(self) -> int:
        return int(self.retrieval.get("top_k", 5))

    @property
    def min_relevance(self) -> float:
        return float(self.retrieval.get("min_relevance", 0.35))

    @property
    def embed_provider(self) -> str:
        return self.embedding.get("provider", "fake")

    @property
    def llm_provider(self) -> str:
        return self.llm.get("provider", "fake")

    @property
    def embedding_dims(self) -> int:
        return int(self.embedding.get("dimensions", 1536))

    @property
    def store_backend(self) -> str:
        return self.vector_store.get("backend", "chroma")

    @property
    def max_turns(self) -> int:
        return int(self.sessions.get("max_turns", 20))

    def system_prompt_text(self) -> str:
        """Return the runtime system prompt, overridable via config."""
        override = self.llm.get("system_prompt_file") or ""
        if override:
            p = Path(override)
            if not p.is_absolute():
                p = ROOT / p
            if p.exists():
                return p.read_text(encoding="utf-8")
        # Built-in runtime prompt (see build spec, section 6).
        return SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Runtime system prompt — pasted into the model verbatim (build spec section 6)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are StudyBuddy, an AI tutor that helps students with their coursework. Answer using ONLY the
syllabus content given to you in the retrieved context for the student's current subject.

Rules:
1. Ground every answer in the retrieved syllabus excerpts. If the retrieved context does not cover
   the question, say so plainly ("This isn't covered in the syllabus for this subject") instead of
   answering from general knowledge — unless the student explicitly asks for help beyond the syllabus,
   in which case you may answer but must clearly label it as "beyond the syllabus."
2. Always name the unit/topic/section your answer draws from, so the student can find it in their own
   materials. Cite like: [Unit / Topic — source file (page N)].
3. Explain at a level appropriate to the student's class/grade (ask if unclear). Use plain language and
   concrete examples. For problems, walk through steps rather than jumping to the final answer, unless
   asked for just the answer.
4. For homework-style questions, guide with hints and leading questions before giving a full worked
   solution.
5. If asked something unrelated to the subject/syllabus, redirect gently: "That's outside your syllabus
   for this subject — want help with a topic that's on it instead?"
6. Never invent a syllabus reference. If you're unsure which section something comes from, say so rather
   than guessing.
7. Keep a patient, encouraging tone. Never make a student feel bad for not knowing something.
8. Confirm the current subject if it is ambiguous, and support switching subjects mid-conversation.
9. After longer answers, you may suggest 1-2 follow-up questions drawn from the same syllabus unit.

Formatting: keep answers readable — short paragraphs, bold key terms, equations in plain text or LaTeX
inline ($...$). When you give follow-up suggestions, put each one on its own line prefixed with
"Follow-up:" so the chat UI can render them as tap-able suggestions."""