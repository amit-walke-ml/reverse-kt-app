from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_admin, user_can_use_session
from app.models.user_models import SessionOwnership, User
from app.schemas.kt import ProcessingStatus, UploadResponse, UploadStatusOut
from app.services.kt_pipeline import iter_input_files, process_session, sanitize_filename
from app.services.session_store import session_store

router = APIRouter(prefix="/upload", tags=["upload"])


def _schedule_pipeline(session_id: str) -> None:
    """Run blocking PDF/LLM/RAG pipeline off the event loop so status polls keep working."""

    asyncio.create_task(asyncio.to_thread(process_session, session_id))


def _knowledge_totals(payload) -> dict:
    units = payload.units if payload else []
    total_concepts = sum(len(u.concepts) for u in units)
    total_pdf = sum(1 for u in units if u.source_type == "pdf")
    total_tr = sum(1 for u in units if u.source_type == "transcript")
    return {
        "summary": getattr(payload, "summary", ""),
        "units": [u.model_dump() for u in units],
        "total_concepts": total_concepts,
        "total_from_pdf": total_pdf,
        "total_from_transcript": total_tr,
    }


@router.post("", response_model=UploadResponse)
async def upload_files(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    files: list[UploadFile] = File(...),
) -> UploadResponse:
    from app.core.config import settings

    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    mb_limit = settings.max_upload_mb * 1024 * 1024

    session_id = session_store.new_session_id()
    session_store.init_session(session_id)
    inp = session_store.session_dir(session_id) / "input"
    inp.mkdir(parents=True, exist_ok=True)

    pdf_n = trans_n = 0
    for uf in files:
        if not uf.filename:
            continue
        contents = await uf.read()
        if len(contents) > mb_limit:
            raise HTTPException(
                status_code=413,
                detail=f"File `{uf.filename}` exceeds max size of {settings.max_upload_mb}MB.",
            )
        ext = "." + uf.filename.rsplit(".", 1)[-1].lower() if "." in uf.filename else ""
        if ext not in {".pdf", ".txt", ".text", ".vtt", ".docx"}:
            raise HTTPException(status_code=400, detail=f"Unsupported file `{uf.filename}`.")

        fname = sanitize_filename(uf.filename)
        (inp / fname).write_bytes(contents)
        if ext == ".pdf":
            pdf_n += 1
        else:
            trans_n += 1

        await uf.close()

    if pdf_n + trans_n == 0:
        raise HTTPException(
            status_code=400,
            detail="Upload at least one supported file: PDF and/or transcript (.txt/.vtt/.docx).",
        )

    db.add(SessionOwnership(session_id=session_id, owner_admin_id=admin.id))
    db.commit()
    _schedule_pipeline(session_id)
    return UploadResponse(session_id=session_id, status=ProcessingStatus.pending)


@router.get("/{session_id}/status", response_model=UploadStatusOut)
async def upload_status(
    session_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UploadStatusOut:
    if not user_can_use_session(db, user, session_id):
        raise HTTPException(status_code=403, detail="Not allowed for this session")
    data = session_store.get_status(session_id)
    return UploadStatusOut(
        session_id=session_id,
        status=ProcessingStatus(str(data["status"])),
        message=data.get("message"),
    )


@router.get("/{session_id}/knowledge")
async def fetch_knowledge(
    session_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if not user_can_use_session(db, user, session_id):
        raise HTTPException(status_code=403, detail="Not allowed for this session")
    knowledge = session_store.load_knowledge(session_id)
    if not knowledge:
        raise HTTPException(status_code=409, detail="Knowledge not ready for this session.")
    return _knowledge_totals(knowledge)


@router.post("/{session_id}/reprocess")
async def reprocess(
    session_id: str,
    user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> UploadResponse:
    if not user_can_use_session(db, user, session_id):
        raise HTTPException(status_code=403, detail="Not allowed for this session")
    if not iter_input_files(session_id):
        raise HTTPException(status_code=404, detail="Session input files missing; start a new upload.")
    session_store.set_status(session_id, ProcessingStatus.pending)
    _schedule_pipeline(session_id)
    return UploadResponse(session_id=session_id, status=ProcessingStatus.pending)
