"""End-to-end session processing orchestration."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.schemas.kt import ProcessingStatus
from app.services.ingestion.pdf_parser import parse_pdf_bytes
from app.services.ingestion.transcript_ingest import (
    normalize_transcript,
    read_transcript_file,
    segment_transcript_heuristic,
)
from app.services.knowledge.extractor import extract_structured_knowledge
from app.services.processing.chunking import chunk_pdf_blocks, chunk_transcript_segments
from app.services.processing.text_clean import clean_text, collapse_blank_lines
from app.services.rag.faiss_store import save_index_bundle
from app.services.session_store import session_store


logger = logging.getLogger(__name__)


_TRANS_EXT = {".txt", ".text", ".vtt", ".docx"}
_PDF_EXT = ".pdf"


def sanitize_filename(name: str) -> str:
    base = Path(name).name
    base = re.sub(r"[^\w\.-]", "_", base)
    return base[:180] if len(base) > 180 else base


def iter_input_files(session_id: str) -> list[Path]:
    inp = session_store.session_dir(session_id) / "input"
    if not inp.exists():
        return []
    return sorted(p for p in inp.iterdir() if p.is_file())


def process_session(session_id: str) -> None:
    try:
        logger.info("KT pipeline started session_id=%s", session_id)
        session_store.set_status(session_id, ProcessingStatus.processing)

        pdf_parts: list[bytes] = []
        transcript_files: list[tuple[str, bytes]] = []

        for path in iter_input_files(session_id):
            ext = path.suffix.lower()
            data = path.read_bytes()
            if ext == _PDF_EXT:
                pdf_parts.append(data)
            elif ext in _TRANS_EXT:
                transcript_files.append((path.name, data))

        if not pdf_parts:
            raise ValueError("At least one PDF file is required for KT assessment.")
        if not transcript_files:
            raise ValueError("At least one transcript file (.txt/.vtt/.docx) is required.")

        md_segments: list[str] = []
        blocks_all = []
        for pdf_bytes in pdf_parts:
            md, blocks = parse_pdf_bytes(pdf_bytes)
            md_segments.append(md)
            blocks_all.extend(blocks)

        pdf_md = "\n\n---- PDF DOCUMENT BOUNDARY ----\n\n".join(md_segments)
        pdf_md = collapse_blank_lines(clean_text(pdf_md))

        transcript_combined_parts: list[str] = []
        for filename, blob in transcript_files:
            transcript_combined_parts.append(read_transcript_file(filename, blob))
        transcript_raw = "\n\n".join(transcript_combined_parts)
        transcript_norm = normalize_transcript(transcript_raw)
        transcript_norm = collapse_blank_lines(clean_text(transcript_norm))

        session_store.save_raw_sources(session_id, pdf_md, transcript_norm)

        segments = segment_transcript_heuristic(transcript_norm)

        ck_pdf = chunk_pdf_blocks(blocks_all)
        ck_tr = chunk_transcript_segments(segments)
        chunks = ck_pdf + ck_tr

        session_store.save_chunks(session_id, chunks)

        session_store.set_status(session_id, ProcessingStatus.extracting_knowledge)
        knowledge = extract_structured_knowledge(pdf_md, transcript_norm)
        session_store.save_knowledge(session_id, knowledge)

        session_store.set_status(session_id, ProcessingStatus.building_index)
        idx_path, meta_path = session_store.faiss_paths(session_id)
        save_index_bundle(idx_path, meta_path, chunks)

        session_store.set_status(session_id, ProcessingStatus.completed)
        logger.info("KT pipeline completed session_id=%s", session_id)
    except Exception as exc:
        logger.exception("KT pipeline FAILED session_id=%s: %s", session_id, exc)
        session_store.set_status(session_id, ProcessingStatus.failed, message=str(exc))
