"""Ingestion: parse syllabus documents -> headings-aware, token-budgeted chunks.

Supported formats:
  .pdf            pypdf (page numbers attached to every chunk)
  .docx           python-docx (headings from paragraph styles / heading text)
  .txt / .md      plain text or markdown (headings / markdown headers)

Every chunk carries metadata: subject_id, unit/topic, source file, page/section
and a stable id  {subject}::{file_stem}::{index}.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

try:  # token-accurate counting when available; falls back to a word heuristic
    import tiktoken  # type: ignore

    _ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - tiktoken optional
    _ENCODING = None

try:
    from docx import Document as _DocxDocument
except Exception:  # pragma: no cover
    _DocxDocument = None

try:
    from pypdf import PdfReader as _PdfReader  # type: ignore
except Exception:  # pragma: no cover
    _PdfReader = None


@dataclass
class Section:
    """Contiguous slab of text belonging to one unit/topic."""

    unit: str
    page: int | None
    text: str


@dataclass
class ParsedDoc:
    file_name: str
    kind: str  # pdf | docx | txt
    sections: list[Section] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    if _ENCODING is not None:
        try:
            return max(1, len(_ENCODING.encode(text)))
        except Exception:
            pass
    return max(1, int(len(text.split()) * 1.35))  # ~1.35 tokens per English word


def _split_lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in re.split(r"\r?\n", text)]


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return False
    if re.match(r"^\s*#{1,6}\s+", stripped):
        return True
    # The unit keyword must be the *first* token (a title), otherwise body
    # sentences like "see Unit 3 for details" get misdetected as headings.
    return bool(re.match(r"^\s*(?:\*{1,2}\s+)?(Unit|Module|Chapter|Section|Topic|Lesson|Week)\b", stripped, re.IGNORECASE))


def _clean_heading(line: str) -> str:
    s = re.sub(r"^\s*#{1,6}\s*", "", line).strip()
    s = re.sub(r"^[\-\*\s]+", "", s).strip()
    s = s.rstrip(".:-–—")
    m = re.match(
        r"^(Unit|Module|Chapter|Section|Topic|Lesson|Week)\s*([\dIVXLX]+)\s*[.\-:–—]?\s*(.*)$",
        s,
        re.IGNORECASE,
    )
    if m:
        label = f"{m.group(1).title()} {m.group(2)}"
        return f"{label} — {m.group(3).strip()}" if m.group(3).strip() else label
    return s


def _parse_text(path: Path) -> ParsedDoc:
    doc = ParsedDoc(file_name=path.name, kind=path.suffix.lstrip(".").lower())
    lines = _split_lines(path.read_text(encoding="utf-8", errors="replace"))
    unit = "General"
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        body = "\n".join(current).strip()
        if body:
            doc.sections.append(Section(unit=unit, page=None, text=body))
        current = []

    for line in lines:
        if _looks_like_heading(line):
            flush()
            unit = _clean_heading(line)
        else:
            current.append(line)
    flush()
    return doc


def _parse_docx(path: Path) -> ParsedDoc:
    doc = ParsedDoc(file_name=path.name, kind="docx")
    if _DocxDocument is None:
        raise RuntimeError("python-docx is required to read .docx files (pip install python-docx)")
    parsed = _DocxDocument(str(path))
    unit = "General"
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        body = "\n".join(current).strip()
        if body:
            doc.sections.append(Section(unit=unit, page=None, text=body))
        current = []

    for para in parsed.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower() if para.style else ""
        if style.startswith("heading") or _looks_like_heading(text):
            flush()
            unit = _clean_heading(text)
        else:
            current.append(text)
    flush()
    return doc


def _parse_pdf(path: Path) -> ParsedDoc:
    doc = ParsedDoc(file_name=path.name, kind="pdf")
    if _PdfReader is None:
        raise RuntimeError("pypdf is required to read .pdf files (pip install pypdf)")
    reader = _PdfReader(str(path))
    unit = "General"
    current: list[str] = []
    current_page: int | None = None

    def flush(page_nr: int | None) -> None:
        nonlocal current, current_page
        body = "\n".join(current).strip()
        if body:
            doc.sections.append(Section(unit=unit, page=current_page, text=body))
        current, current_page = [], page_nr

    for idx, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for raw in _split_lines(text):
            line = raw.strip()
            if not line:
                continue
            if _looks_like_heading(line):
                flush(idx)
                unit = _clean_heading(line)
            else:
                current.append(line)
                current_page = idx
    flush(current_page)
    return doc


def parse_document(path: Path) -> ParsedDoc:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(path)
    if suffix == ".docx":
        return _parse_docx(path)
    if suffix in {".txt", ".md", ".markdown"}:
        return _parse_text(path)
    raise ValueError(f"Unsupported document type: {suffix} (expected .pdf/.docx/.txt/.md)")


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])|\n\s*\n")


def _sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p.strip()]
    out: list[str] = []
    for part in parts:
        if estimate_tokens(part) > 400:  # over-long run — split harder
            sub = re.split(r"(?<=[,;:])\s+(?=\S)", part)
            out.extend(s.strip() for s in sub if s.strip())
        else:
            out.append(part)
    return out


def _hard_split(text: str, size_tokens: int) -> list[str]:
    """Fallback: split a too-long sentence on character windows."""
    words = text.split()
    char_budget = max(200, size_tokens * 4)
    out: list[str] = []
    buf: list[str] = []
    for w in words:
        buf.append(w)
        if len(" ".join(buf)) >= char_budget:
            out.append(" ".join(buf))
            buf = []
    if buf:
        out.append(" ".join(buf))
    return out


def chunk_section(section: Section, *, target_tokens: int, overlap_ratio: float) -> list[str]:
    """Sliding-window chunk of one section (~target tokens, ~overlap_ratio overlap)."""
    if not section.text.strip():
        return []
    sentences = _sentences(section.text)
    if not sentences:
        return _hard_split(section.text, target_tokens)

    sizes = [estimate_tokens(s) for s in sentences]
    chunks: list[str] = []
    n = len(sentences)
    start = 0
    step = max(1, int(target_tokens * (1.0 - overlap_ratio)))
    while start < n:
        end, total = start, 0
        while end < n and total + sizes[end] <= target_tokens:
            total += sizes[end]
            end += 1
        if end == start:  # one oversized sentence — keep whole
            chunks.append(sentences[start])
            start += 1
            continue
        chunks.append(" ".join(sentences[start:end]).strip())

        advance, carry = start, 0
        while advance < end and carry < step:
            carry += sizes[advance]
            advance += 1
        start = advance if advance > start else start + 1
    return chunks


def collect_documents(folder: Path) -> list[Path]:
    supported = {".pdf", ".docx", ".txt", ".md", ".markdown"}
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in supported)


def chunk_document(doc: ParsedDoc, *, target_tokens: int, overlap_ratio: float) -> list[tuple[str, dict]]:
    """Return [(chunk_text, metadata)] for one parsed document."""
    out: list[tuple[str, dict]] = []
    for sec in doc.sections:
        for text in chunk_section(sec, target_tokens=target_tokens, overlap_ratio=overlap_ratio):
            out.append(
                (
                    text,
                    {
                        "unit": sec.unit,
                        "file": doc.file_name,
                        "kind": doc.kind,
                        "page": str(sec.page) if sec.page is not None else "",
                    },
                )
            )
    return out


def ingest_folder(
    folder: Path,
    *,
    subject_id: str,
    target_tokens: int,
    overlap_ratio: float,
) -> tuple[int, int, list[tuple[str, dict]]]:
    """Parse every syllabus file under `folder`.

    Returns (file_count, chunk_count, [(chunk_text, metadata), ...]).
    """
    files = collect_documents(folder)
    all_chunks: list[tuple[str, dict]] = []
    for path in files:
        try:
            parsed = parse_document(path)
        except Exception as exc:  # surface clear per-file errors, keep going
            raise RuntimeError(f"Failed to parse {path.name}: {exc}") from exc
        for text, meta in chunk_document(parsed, target_tokens=target_tokens, overlap_ratio=overlap_ratio):
            all_chunks.append((text, {**meta, "subject_id": subject_id}))
    return len(files), len(all_chunks), all_chunks