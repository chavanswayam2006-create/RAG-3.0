# StudyBuddy — Syllabus-Grounded RAG Chatbot

A chatbot that helps students study by answering questions **strictly grounded in their
subject's syllabus** — not generic internet knowledge. Every answer cites the exact
syllabus unit/document it comes from, and questions that fall outside the syllabus are
flagged instead of guessed.

Built spec-first (per the build prompt). Multi-subject by design: **adding a subject =
adding a folder of documents + one config entry. No code changes.**

```
                          ┌────────────────────────────────────────────┐
  /data/math/*.txt|pdf    │  app/ingest.py (chunk ~650 tok, 15% ovlp)  │
  /data/physics/*.docx ─▶ │  app/embedders.py → vectors                │
                          │  app/store.py    → Chroma / numpy fallback │
                          └────────────────────────────────────────────┘
                                        │ subject-scoped retrieval
   browser (static chat UI) ── /api/chat │ top-k(5) + relevance ≥ 0.35
                          ┌──────────────▼─────────────────────────────┐
                          │ app/chat.py: Claude / FakeLLM + system     │
                          │ prompt (runtime rules, §6 of the spec)     │
                          └────────────────────────────────────────────┘
```

## Quick start

```bash
# 1. (optional) secrets — real answers need an Anthropic key
copy .env.example .env        # then fill in ANTHROPIC_API_KEY etc.

# 2. install
pip install -r requirements.txt
pip install chromadb tiktoken reportlab   # prebuilts for the demo data/pdf generator

# 3. ingest the syllabus folders in config.yaml
python ingest.py

# 4. (optional) regenerate the sample .docx/.pdf demo documents
python scripts/make_samples.py && python ingest.py

# 5. run the app
uvicorn app.main:app --reload --port 8000
#    open http://localhost:8000
```

The repo ships with two sample subjects (`data/math/`, `data/physics/`) so everything
works out of the box. The APIs powering it are testable directly:

```bash
curl http://localhost:8000/api/health
curl -X POST http://localhost:8000/api/chat -H "Content-Type: application/json" \
  -d '{"subject_id":"math","message":"How do I solve x^2 - 5x + 6 = 0?"}'
```

After any change, the full pipeline can be verified automatically:

```bash
python scripts/smoke_test.py    # boots a server, runs 14 checks, prints PASS/FAIL
```

## Offline demo mode (no API keys)

The app runs **without any API keys** by setting these in `config.yaml`:

```yaml
embedding:
  provider: "fake"        # deterministic word-hash vectors (local)
llm:
  provider: "fake"        # template answer built from the top retrieved chunk
```

Answers are clearly labelled as demo answers. Flip `provider` to `"openai"` /
`"anthropic"` and add keys to `.env` for the real pipeline. You can also run the real
embedders with the fake LLM or vice-versa — providers are independent.

## Adding a subject

1. `mkdir data/<subject>` and drop syllabus files in it — PDF, DOCX, TXT or Markdown are
   supported (pages are captured automatically for PDFs).
2. Add one block to the `subjects:` registry in `config.yaml`:

   ```yaml
   subjects:
     biology:
       label: "Biology"
       path: "data/biology"
       blurb: "Cells · Genetics · Ecology"
   ```

3. `python ingest.py --subject biology`

That's it — the frontend dropsheet and `/api/subjects` pick the new subject up at reload.

## Configuration (`config.yaml`)

| Key | What it controls |
|---|---|
| `subjects` | subject id → label / folder / blurb registry |
| `embedding.provider` | `openai` · `voyage` · `fake` |
| `llm.provider` | `anthropic` · `fake` (modify `model` / `temperature`) |
| `retrieval.top_k` | chunks fetched per question (default 5) |
| `retrieval.min_relevance` | cosine threshold — below it → **"no syllabus match"**, no hallucination (0.35) |
| `retrieval.chunk_tokens` / `chunk_overlap` | window size and overlap (650 tok / 15%) |
| `vector_store.backend` | `chroma` (default) with automatic fallback to `local` (numpy) if `chromadb` can't import; `local` forces numpy |
| `sessions.max_turns` | how much recent history goes to the LLM |

The **runtime system prompt** (StudyBuddy rules — grounding, citations, tone, redirects,
subject switching) lives in `app/config.py` (`SYSTEM_PROMPT`) and may be overridden with
`llm.system_prompt_file: "path/to/prompt.txt"`.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | chat frontend |
| GET | `/api/health` | provider + store status |
| GET | `/api/subjects` | subjects with document/chunk counts |
| POST | `/api/chat` | `{subject_id, message, session_id?, history?}` → answer + sources + follow-ups |
| GET | `/api/sessions` | session list (title, message count, age) |
| GET | `/api/sessions/{id}` | full transcript (restores a chat) |
| DELETE | `/api/sessions/{id}` | remove a session |

`/api/chat` response:
```json
{
  "session_id": "a1b2c3d4e5f6",
  "status": "answered",
  "reply": "Based on **Unit 3 — Quadratic Equations** …",
  "sources": [{ "unit": "Unit 3 — Quadratic Equations", "file": "syllabus.txt",
                "page": null, "excerpt": "…", "score": 0.72 }],
  "followups": ["When can you solve by factorising vs the formula?"]
}
```

## Grounding behaviour

- Retrieval is **hard-scoped to the selected subject** (Chroma `where={"subject_id": …}`
  / per-subject numpy files). Subject A's content can never leak into a subject B query.
- If the best chunk scores below `min_relevance`, the bot answers **"This isn't covered
  in the syllabus…"** without calling the LLM — it never invents content.
- Every grounded answer carries **expandable sources** in the UI: unit, source file,
  page (PDFs), excerpt, and a relevance bar. The system prompt additionally forces the
  model to name the unit inside the answer.
- `status: no_match` messages are rendered as info cards in the chat, `error` messages
  as error cards.

## Project layout

```
config.yaml              subjects registry + pipeline settings
ingest.py                CLI: ingest/embed/store a subject's documents
app/
  config.py              YAML loader + runtime system prompt
  models.py              pydantic request/response models
  ingest.py              PDF/DOCX/TXT parsers + heading-aware, overlapping chunker
  embedders.py           openai / voyage / fake (word-hash) embedding providers
  llms.py                anthropic / fake generation providers
  store.py               Chroma + numpy/local backends (subject-scoped search)
  chat.py                retrieval → threshold → prompt → LLM → follow-up extraction
  history.py             per-session JSON persistence (data/sessions/)
  main.py                FastAPI app + static frontend hosting
static/                  chat UI (index.html / styles.css / app.js)
data/<subject>/          syllabus documents (this is where you add subjects)
scripts/make_samples.py  regenerates sample .docx/.pdf demo documents
```

## Notes / next steps

- Embeddings & LLM calls go through thin provider classes — swapping in Voyage
  (`embedding.provider: voyage`) or a different model is a config change.
- `chromadb` is optional: if the wheel isn't available for your Python version the store
  transparently falls back to the numpy `local` backend (fine for ~10k chunks).
- For production: add auth, rate limiting, and swap the store for Pinecone/Qdrant/Weaviate
  behind the `VectorStore` interface; keep an eye on the §7 evaluation checklist
  (retrieval precision, citation accuracy, coverage gaps, latency, tone).