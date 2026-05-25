"""Upload endpoints — File upload and processing pipeline."""

import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, UploadFile, File, BackgroundTasks, HTTPException

from app.config import get_settings
from app.models.schemas import (
    UploadResponse,
    StatusResponse,
    SessionInfo,
    ProcessingStatus,
    SourceType,
    ExtractedKnowledge,
)
from app.services.pdf_parser import PDFParser
from app.services.transcript_parser import TranscriptParser
from app.services.text_processor import TextProcessor
from app.services.knowledge_extractor import KnowledgeExtractor
from app.services.vector_store import VectorStore
from app.utils.helpers import generate_session_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/upload", tags=["upload"])

# In-memory session store (persisted to disk)
sessions: dict[str, SessionInfo] = {}
session_knowledge: dict[str, ExtractedKnowledge] = {}
session_vector_stores: dict[str, VectorStore] = {}


def _get_session_dir(session_id: str) -> Path:
    settings = get_settings()
    session_dir = settings.PROCESSED_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


@router.post("", response_model=UploadResponse)
async def upload_files(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
):
    """Upload PDF and/or transcript files for processing."""
    settings = get_settings()
    session_id = generate_session_id()
    upload_dir = settings.UPLOAD_DIR / session_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    source_types = []

    for file in files:
        if not file.filename:
            continue

        # Determine file type
        suffix = Path(file.filename).suffix.lower()
        if suffix == ".pdf":
            source_types.append(SourceType.PDF)
        elif suffix in (".vtt", ".txt", ".text", ".docx"):
            source_types.append(SourceType.TRANSCRIPT)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {suffix}. "
                       f"Supported: .pdf, .vtt, .txt, .docx"
            )

        # Save file
        file_path = upload_dir / file.filename
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        saved_files.append(file.filename)
        logger.info(f"Saved file: {file.filename} ({len(content)} bytes)")

    if not saved_files:
        raise HTTPException(status_code=400, detail="No valid files uploaded")

    # Create session
    session = SessionInfo(
        session_id=session_id,
        status=ProcessingStatus.PENDING,
        files_uploaded=saved_files,
        source_types=list(set(source_types)),
    )
    sessions[session_id] = session

    # Start background processing
    background_tasks.add_task(process_session, session_id)

    return UploadResponse(
        session_id=session_id,
        message=f"Uploaded {len(saved_files)} file(s). Processing started.",
        files_received=saved_files,
        status=ProcessingStatus.PENDING,
    )


async def process_session(session_id: str):
    """Background task to process uploaded files through the full pipeline."""
    settings = get_settings()
    session = sessions.get(session_id)
    if not session:
        return

    upload_dir = settings.UPLOAD_DIR / session_id
    session_dir = _get_session_dir(session_id)

    try:
        session.status = ProcessingStatus.PROCESSING

        # Initialize services
        pdf_parser = PDFParser()
        transcript_parser = TranscriptParser()
        text_processor = TextProcessor()
        knowledge_extractor = KnowledgeExtractor()
        vector_store = VectorStore()

        all_chunks = []

        # Process each uploaded file
        for filename in session.files_uploaded:
            file_path = upload_dir / filename
            suffix = Path(filename).suffix.lower()

            if suffix == ".pdf":
                logger.info(f"Processing PDF: {filename}")
                sections = pdf_parser.parse(file_path)
                chunks = text_processor.process_pdf_sections(sections, filename)
                all_chunks.extend(chunks)

            elif suffix in (".vtt", ".txt", ".text", ".docx"):
                logger.info(f"Processing transcript: {filename}")
                transcript = transcript_parser.parse(file_path)
                chunks = text_processor.process_transcript(transcript, filename)
                all_chunks.extend(chunks)

        if not all_chunks:
            session.status = ProcessingStatus.FAILED
            session.error_message = "No content extracted from uploaded files"
            return

        logger.info(f"Total chunks: {len(all_chunks)}")

        # Extract structured knowledge
        session.status = ProcessingStatus.EXTRACTING_KNOWLEDGE
        knowledge = knowledge_extractor.extract_from_chunks(all_chunks, session_id)
        session_knowledge[session_id] = knowledge

        # Save knowledge to disk
        knowledge_path = session_dir / "knowledge.json"
        with open(knowledge_path, "w", encoding="utf-8") as f:
            json.dump(knowledge.model_dump(), f, indent=2, default=str)

        # Build vector index
        session.status = ProcessingStatus.BUILDING_INDEX
        vector_store.build_index(all_chunks, session_id)
        session_vector_stores[session_id] = vector_store

        # Update session
        session.status = ProcessingStatus.COMPLETED
        session.knowledge_summary = knowledge.summary

        logger.info(f"Session {session_id} processing completed")

    except Exception as e:
        logger.exception(f"Processing failed for session {session_id}: {e}")
        session.status = ProcessingStatus.FAILED
        session.error_message = str(e)


@router.get("/{session_id}/status", response_model=StatusResponse)
async def get_status(session_id: str):
    """Get processing status for a session."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return StatusResponse(
        session_id=session_id,
        status=session.status,
        message=session.error_message if session.status == ProcessingStatus.FAILED
               else f"Status: {session.status.value}",
        details={
            "files": session.files_uploaded,
            "has_knowledge": session_id in session_knowledge,
            "questions_generated": session.questions_generated,
        },
    )


@router.get("/{session_id}/knowledge")
async def get_knowledge(session_id: str):
    """Get extracted knowledge for a session."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status != ProcessingStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Processing not complete. Current status: {session.status.value}"
        )

    knowledge = session_knowledge.get(session_id)
    if not knowledge:
        # Try loading from disk
        session_dir = _get_session_dir(session_id)
        knowledge_path = session_dir / "knowledge.json"
        if knowledge_path.exists():
            with open(knowledge_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                knowledge = ExtractedKnowledge(**data)
                session_knowledge[session_id] = knowledge
        else:
            raise HTTPException(status_code=404, detail="Knowledge not found")

    return knowledge.model_dump()
