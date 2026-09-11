"""Pydantic request/response models shared by the API."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    subject_id: str = Field(min_length=1, description="Subject to anchor the answer to")
    message: str = Field(min_length=1, max_length=4000)
    session_id: Optional[str] = None
    history: list[dict] = Field(
        default_factory=list,
        description="Client-side turns [{role, content}]; backfilled by the server store if empty.",
    )


class SourceRef(BaseModel):
    """One retrieved syllabus chunk surfaced as a citation."""

    unit: str = "General"
    file: str = ""
    page: Optional[str] = None
    excerpt: str = ""
    score: float = 0.0


class ChatResponse(BaseModel):
    session_id: str
    status: Literal["answered", "no_match", "error"]
    reply: str
    sources: list[SourceRef] = []
    followups: list[str] = []


class SubjectInfo(BaseModel):
    id: str
    label: str
    path: str
    blurb: str = ""
    documents: int = 0
    chunks: int = 0


class SubjectsResponse(BaseModel):
    subjects: list[SubjectInfo]
    mode: dict = {}


class SessionResponse(BaseModel):
    session_id: str
    turns: list[dict]


class HealthResponse(BaseModel):
    status: str
    subjects: list[str]
    embedder: str
    llm: str
    vector_store: str