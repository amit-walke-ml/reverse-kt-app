from __future__ import annotations

import hashlib
import re

from app.core.config import settings
from app.schemas.kt import ChunkRecord
from app.services.ingestion.pdf_parser import PdfBlock
from app.services.processing.text_clean import clean_text


def _chunk_id(prefix: str, text: str) -> str:
    h = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{prefix}_{h}"


def chunk_pdf_blocks(blocks: list[PdfBlock]) -> list[ChunkRecord]:
    out: list[ChunkRecord] = []
    buf: list[str] = []
    buf_titles: list[str] = []

    def flush() -> None:
        nonlocal buf, buf_titles
        text = "\n\n".join(buf).strip()
        if len(text) < 40:
            buf, buf_titles = [], []
            return
        title = buf_titles[-1] if buf_titles else "Document"
        path = " / ".join(buf_titles[-3:]) if buf_titles else title
        cid = _chunk_id("pdf", text + path)
        out.append(
            ChunkRecord(
                id=cid,
                text=text,
                source="pdf",
                title=title,
                section_path=path,
            )
        )
        buf, buf_titles = [], []

    for b in blocks:
        if b.level == "title":
            if buf and sum(len(x) for x in buf) >= settings.chunk_target_chars // 2:
                flush()
            buf_titles = b.section_titles + [b.content]
            buf.append(f"## {b.content}")
            continue

        piece = b.content
        if b.level == "list":
            piece = "- " + b.content
        if b.level == "table":
            piece = b.content

        prospective = sum(len(x) for x in buf) + len(piece)
        if prospective > settings.chunk_target_chars and buf:
            flush()
            buf = [piece]
            buf_titles = (b.section_titles + []).copy()
            if b.level != "title" and not buf_titles:
                buf_titles = ["General"]
        else:
            buf.append(piece)
            buf_titles = (b.section_titles + []).copy()
            if b.level != "title" and not buf_titles:
                buf_titles = ["General"]

    flush()
    return out


def chunk_transcript_segments(segments: list[dict[str, str]]) -> list[ChunkRecord]:
    out: list[ChunkRecord] = []
    for seg in segments:
        text = clean_text(seg["text"])
        if len(text) < 40:
            continue
        label = seg.get("label") or "Transcript"
        sid = seg.get("id") or _chunk_id("tr", text)

        if len(text) <= settings.chunk_target_chars:
            out.append(
                ChunkRecord(
                    id=_chunk_id("tr", text + sid),
                    text=f"[TRANSCRIPT — {label}]\n\n{text}",
                    source="transcript",
                    title=label[:200],
                    section_path=("Transcript / " + label)[:240],
                )
            )
            continue

        sentences = re.split(r"(?<=[.!?])\s+", text)
        buf: list[str] = []
        char_count = 0
        part = 0

        def flush_buf() -> None:
            nonlocal buf, char_count, part
            chunk_text = " ".join(buf).strip()
            if len(chunk_text) < 40:
                buf, char_count = [], 0
                return
            part += 1
            out.append(
                ChunkRecord(
                    id=_chunk_id("tr", chunk_text + sid + str(part)),
                    text=f"[TRANSCRIPT — {label} — part {part}]\n\n{chunk_text}",
                    source="transcript",
                    title=f"{label[:120]} (part {part})",
                    section_path=f"Transcript / {label}"[:240],
                )
            )
            overlap = settings.chunk_overlap_chars
            tail = chunk_text[-overlap:] if len(chunk_text) > overlap else ""
            buf = [tail] if tail else []
            char_count = len(tail)

        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if char_count + len(s) > settings.chunk_target_chars and buf:
                flush_buf()
            buf.append(s)
            char_count += len(s) + 1
        flush_buf()

    return out
