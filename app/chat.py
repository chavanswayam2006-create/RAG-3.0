"""Retrieval + generation pipeline.

1. embed the question
2. retrieve top-k chunks scoped to the selected subject
3. below the relevance threshold -> answer "not in syllabus" (no LLM call)
4. otherwise ask the LLM with retrieved context + history + runtime system prompt
5. parse follow-up suggestions ("Follow-up: ..." lines) out of the reply
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .store import SearchHit, VectorStore

FOLLOWUP_RE = re.compile(r"^\s*[-\*]?\s*(?:Follow-?up(?: question)?s?|Suggested|Try asking)[:.]?\s*(.+)$", re.IGNORECASE)


@dataclass
class Answer:
    status: str  # answered | no_match | error
    reply: str
    sources: list[SearchHit] = field(default_factory=list)
    followups: list[str] = field(default_factory=list)


def _passage_block(hits: list[SearchHit]) -> str:
    blocks = []
    for i, hit in enumerate(hits, start=1):
        meta = hit.metadata
        page = f", page {meta['page']}" if meta.get("page") else ""
        blocks.append(
            f"[{i}] Unit: {meta.get('unit', 'General')} | Source: {meta.get('file', '?')}{page}\n{hit.text}"
        )
    return "\n\n".join(blocks)


def _history_block(history: list[dict], max_turns: int = 12) -> str:
    if not history:
        return ""
    lines = ["Recent conversation:"]
    for turn in history[-max_turns:]:
        role = turn.get("role", "user")
        lines.append(f"{'Student' if role == 'user' else 'StudyBuddy'}: {str(turn.get('content', ''))[:600]}")
    return "\n".join(lines)


def build_user_prompt(subject_label: str, history: list[dict], question: str, hits: list[SearchHit]) -> str:
    parts = [
        f"Student's current subject: {subject_label}",
        f"Student's question: {question}",
    ]
    hist = _history_block(history)
    if hist:
        parts.append(hist)
    if hits:
        parts.append(
            "Retrieved syllabus passages for THIS question. Ground your answer in these passages ONLY, "
            "and cite the Unit + source file for every claim:"
        )
        parts.append(_passage_block(hits))
    else:
        parts.append("No syllabus passage cleared the relevance threshold for this question.")
    parts.append(
        "Answer per your system rules. If you end with follow-up suggestions, put each on its own line "
        "prefixed with 'Follow-up:'."
    )
    return "\n\n".join(parts)


def extract_followups(reply: str, max_items: int = 3) -> tuple[str, list[str]]:
    """Split 'Follow-up:' suggestion lines out of the assistant reply."""
    kept: list[str] = []
    links: list[str] = []
    for raw in reply.splitlines():
        m = FOLLOWUP_RE.match(raw)
        if m:
            links.append(m.group(1).strip().strip("\"'`"))
        else:
            kept.append(raw)
    return "\n".join(kept).strip(), links[:max_items]


class ChatPipeline:
    def __init__(self, settings, store: VectorStore, embedder, llm, session_store) -> None:
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self.llm = llm
        self.sessions = session_store

    def answer(self, subject_id: str, question: str, history: list[dict]) -> Answer:
        subject = self.settings.subjects.get(subject_id, {})
        label = subject.get("label", subject_id)
        system = self.settings.system_prompt_text()

        try:
            query_vector = self.embedder.embed_one(question)
            hits = self.store.search(subject_id, query_vector, self.settings.top_k)
            # The deterministic 'fake' embedder (offline smoke tests) compresses
            # absolute cosine scores, so relax the threshold for it. Real
            # embedders (OpenAI/Voyage) keep the strict config value.
            min_rel = self.settings.min_relevance
            if getattr(self.embedder, "name", "") == "fake":
                min_rel = min(min_rel, 0.08)
            hits = [h for h in hits if h.score >= min_rel][: self.settings.top_k]
        except Exception as exc:
            return Answer(status="error", reply=f"Something went wrong while searching the syllabus. ({exc})")

        if not hits:
            return Answer(
                status="no_match",
                reply=(
                    f"This isn't covered in the syllabus for **{label}** — I couldn't find a matching passage, "
                    "and I won't guess from general knowledge. Try rephrasing, ask about a topic that is on the "
                    "syllabus, or switch subjects."
                ),
                followups=[],
            )

        user_prompt = build_user_prompt(label, history, question, hits)
        why = " (demo mode)" if self.llm.name == "fake" else ""
        try:
            raw = self.llm.generate(system, user_prompt, sources=hits, subject_label=label)
            reply, followups = extract_followups(raw)
            if not reply:
                reply = raw
            return Answer(status="answered", reply=reply, sources=hits, followups=followups)
        except Exception as exc:
            return Answer(
                status="error",
                reply=f"The tutor service failed to generate an answer ({exc}). Please try again shortly.",
                sources=hits,
            )