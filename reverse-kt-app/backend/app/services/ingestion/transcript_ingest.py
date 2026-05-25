"""Transcript ingestion: Teams VTT, plain text, docx — no video/audio."""

from __future__ import annotations

import io
import re
from pathlib import Path

import webvtt
from docx import Document


def read_transcript_file(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".vtt":
        return _parse_vtt_bytes(data)
    if ext in (".txt", ".text"):
        return data.decode("utf-8", errors="replace")
    if ext == ".docx":
        return _parse_docx_bytes(data)
    raise ValueError(f"Unsupported transcript format: {ext}")


def _parse_docx_bytes(data: bytes) -> str:
    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _parse_vtt_bytes(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    lines: list[str] = []
    for caption in webvtt.from_string(text):
        cue = " ".join(caption.text.strip().split())
        if cue:
            lines.append(cue)
    return "\n".join(lines)


_FILLER_PATTERN = re.compile(
    r"\b(um+|uh+|erm+|hmm+|uhm+|mmm+|like+|you\s+know+)\b[,.\s]*",
    re.IGNORECASE,
)
_NOISE_PARENS = re.compile(r"\[[^\]]{0,120}\]")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")


def normalize_transcript(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _NOISE_PARENS.sub(" ", text)
    text = _FILLER_PATTERN.sub("", text)

    paragraphs: list[str] = []
    for para in text.split("\n\n"):
        p = para.strip()
        if not p:
            continue
        merged = " ".join(p.split())
        merged = _MULTI_SPACE.sub(" ", merged)
        if len(merged) < 3:
            continue
        paragraphs.append(merged)
    return "\n\n".join(paragraphs)


def segment_transcript_heuristic(text: str, max_segment_chars: int = 3500) -> list[dict[str, str]]:
    """Split transcript into coarse segments (topic threads) for chunking and LLM passes."""
    if not text.strip():
        return []

    # Prefer speaker-style boundaries: "Name: message" repeated
    speaker_split = re.split(r"\n(?=[A-Za-z][A-Za-z0-9 .,'-]{0,40}:\s)", text)
    chunks: list[str] = []
    buf = ""
    for part in speaker_split:
        part = part.strip()
        if not part:
            continue
        if len(buf) + len(part) + 2 <= max_segment_chars:
            buf = f"{buf}\n\n{part}".strip()
        else:
            if buf:
                chunks.append(buf)
            buf = part
    if buf:
        chunks.append(buf)

    out: list[dict[str, str]] = []
    for i, c in enumerate(chunks):
        label = _infer_segment_label(c)
        out.append({"id": f"ts_{i+1}", "label": label, "text": c})
    return out


def _infer_segment_label(segment: str) -> str:
    first = segment.strip().split("\n", 1)[0].strip()
    if len(first) > 90:
        return first[:87] + "..."
    return first or f"Segment"
