"""StudyBuddy FastAPI application.

Endpoints
  GET  /                     chat frontend
  GET  /api/health
  GET  /api/subjects
  GET  /api/sessions         prior sessions list
  GET  /api/sessions/{id}    full transcript for a session
  POST /api/chat             {subject_id, message, session_id?, history?}
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .chat import ChatPipeline
from .config import ROOT, Settings
from .embedders import create_embedder
from .history import SessionStore
from .llms import create_llm
from .models import ChatRequest, ChatResponse, HealthResponse, SessionResponse, SourceRef, SubjectInfo, SubjectsResponse
from .store import create_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("studybuddy")

STATIC_DIR = ROOT / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    store = create_store(settings)
    embedder = create_embedder(settings)
    llm = create_llm(settings)
    sessions = SessionStore(settings.sessions_dir)
    pipeline = ChatPipeline(settings, store, embedder, llm, sessions)
    app.state.settings, app.state.store = settings, store
    app.state.embedder, app.state.llm = embedder, llm
    app.state.sessions, app.state.pipeline = sessions, pipeline
    log.info(
        "StudyBuddy ready | store=%s | embedder=%s | llm=%s | subjects=%s",
        store.backend, embedder.name, llm.name, ", ".join(settings.subjects),
    )
    yield


app = FastAPI(title="StudyBuddy", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local dev; tighten for deployment
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", response_model=HealthResponse)
def health():
    s: Settings = app.state.settings
    return HealthResponse(
        status="ok",
        subjects=list(s.subjects),
        embedder=app.state.embedder.name,
        llm=app.state.llm.name,
        vector_store=app.state.store.backend,
    )


@app.get("/api/subjects", response_model=SubjectsResponse)
def subjects():
    s: Settings = app.state.settings
    store = app.state.store
    items = []
    for sid, meta in s.subjects.items():
        folder = s.subject_folders()[sid]
        docs = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".txt", ".md", ".markdown"}] if folder.exists() else []
        items.append(
            SubjectInfo(
                id=sid,
                label=meta.get("label", sid),
                path=str(folder),
                blurb=meta.get("blurb", ""),
                documents=len(docs),
                chunks=store.count(sid),
            )
        )
    return SubjectsResponse(
        subjects=items,
        mode={
            "embedder": app.state.embedder.name,
            "llm": app.state.llm.name,
            "vector_store": app.state.store.backend,
        },
    )


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    s: Settings = app.state.settings
    sessions: SessionStore = app.state.sessions
    pipeline: ChatPipeline = app.state.pipeline

    if req.subject_id not in s.subjects:
        return ChatResponse(
            session_id=req.session_id or "",
            status="error",
            reply=f"Unknown subject '{req.subject_id}'. Available: {', '.join(s.subjects)}.",
        )

    session_id = req.session_id or sessions.new_id()
    if req.history and not sessions.get(session_id):
        sessions.seed(session_id, req.history)

    user_turn = {"role": "user", "content": req.message, "subject_id": req.subject_id, "ts": int(__import__("time").time())}
    sessions.append(session_id, user_turn)
    history = sessions.latest_turns(session_id, max_turns=s.max_turns)
    prior = [t for t in history if t is not user_turn]

    answer = pipeline.answer(req.subject_id, req.message, prior)

    sources = [
        SourceRef(
            unit=h.metadata.get("unit", "General"),
            file=h.metadata.get("file", ""),
            page=h.metadata.get("page") or None,
            excerpt=h.text,
            score=round(h.score, 3),
        )
        for h in answer.sources
    ]
    sessions.append(
        session_id,
        {
            "role": "assistant",
            "content": answer.reply,
            "subject_id": req.subject_id,
            "status": answer.status,
            "sources": [s.model_dump() for s in sources],
            "ts": int(__import__("time").time()),
        },
    )
    return ChatResponse(
        session_id=session_id,
        status=answer.status,
        reply=answer.reply,
        sources=sources,
        followups=answer.followups,
    )


@app.get("/api/sessions", include_in_schema=False)
def list_sessions():
    return {"sessions": app.state.sessions.list()}


@app.get("/api/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str):
    return SessionResponse(session_id=session_id, turns=app.state.sessions.get(session_id))


@app.delete("/api/sessions/{session_id}", include_in_schema=False)
def delete_session(session_id: str):
    app.state.sessions.delete(session_id)
    return {"deleted": session_id}