from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_admin, user_can_use_session
from app.core.config import settings
from app.models.user_models import Assignment, User
from app.schemas.kt import EvaluateBody, EvaluateResponse, GenerateBody, QuestionsOut
from app.services.evaluation.engine import evaluate_answers, load_index_optional
from app.services.questions.generator import generate_question_bank
from app.services.rag.faiss_store import load_index_bundle
from app.services.session_store import session_store

router = APIRouter(prefix="/assessment", tags=["assessment"])

_ASSESSMENT_TIME_EXPIRED = (
    "Assessment time limit expired — sign in again if your administrator allows a new attempt."
)


def _trainee_assignment_row(db: Session, user: User, session_id: str) -> Assignment | None:
    if user.role != "user":
        return None
    return (
        db.query(Assignment)
        .filter(Assignment.session_id == session_id, Assignment.assigned_user_id == user.id)
        .first()
    )


def _apply_trainee_attempt_window(
    db: Session, assignment_row: Assignment
) -> tuple[datetime | None, int | None]:
    """For an unsubmitted trainee assignment: start clock on first call; raise 403 if past limit."""
    if assignment_row.submitted_at is not None:
        return None, None

    now = datetime.now(timezone.utc)
    limit_min = settings.assessment_time_limit_minutes

    if assignment_row.assessment_started_at is None:
        assignment_row.assessment_started_at = now
        db.add(assignment_row)
        db.commit()
        db.refresh(assignment_row)

    start = assignment_row.assessment_started_at
    if start is not None and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    deadline = start + timedelta(minutes=limit_min) if start is not None else None
    if deadline is not None and now > deadline:
        raise HTTPException(status_code=403, detail=_ASSESSMENT_TIME_EXPIRED)

    return deadline, limit_min


@router.get("/runtime-settings")
def assessment_runtime_settings() -> dict:
    """Public sizing for UI (question count and trainee attempt window)."""
    return {
        "assessment_question_count": settings.assessment_question_count,
        "assessment_time_limit_minutes": settings.assessment_time_limit_minutes,
    }


def _public_questions_signature(pub: list) -> str:
    texts = [getattr(q, "question_text", "") or "" for q in pub]
    blob = "\n".join(t.strip().lower() for t in sorted(texts))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@router.post("/{session_id}/generate")
async def trigger_generation(
    session_id: str,
    body: GenerateBody,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if not user_can_use_session(db, admin, session_id):
        raise HTTPException(status_code=403, detail="Not allowed for this session")

    knowledge = session_store.load_knowledge(session_id)
    if not knowledge:
        raise HTTPException(status_code=409, detail="Knowledge graph missing — finish ingestion first.")

    idx_path, meta_path = session_store.faiss_paths(session_id)
    if not idx_path.exists():
        raise HTTPException(status_code=409, detail="Vector index unavailable for this session.")

    generation_id = str(uuid.uuid4())
    prior_signatures = session_store.recent_generation_signatures(session_id)

    index_bundle = load_index_bundle(idx_path, meta_path)
    pub, prv = generate_question_bank(
        index_bundle,
        knowledge,
        difficulty_mix=body.difficulty_mix,
        question_count=settings.assessment_question_count,
        generation_id=generation_id,
        avoid_signatures=prior_signatures,
    )
    session_store.save_questions(session_id, pub, prv)

    fingerprint = _public_questions_signature(pub)

    retries = 0
    max_retries = 2
    while fingerprint in prior_signatures and retries < max_retries:
        retries += 1
        regen_id = f"{generation_id}-retry-{retries}"
        pub, prv = generate_question_bank(
            index_bundle,
            knowledge,
            difficulty_mix=body.difficulty_mix,
            question_count=settings.assessment_question_count,
            generation_id=regen_id,
            avoid_signatures=list({*prior_signatures, fingerprint}),
        )
        session_store.save_questions(session_id, pub, prv)
        fingerprint = _public_questions_signature(pub)

    session_store.append_generation_record(session_id, generation_id, fingerprint)

    return {
        "session_id": session_id,
        "question_count": len(pub),
        "generation_id": generation_id,
        "question_bank_signature": fingerprint,
    }


@router.get("/{session_id}/questions", response_model=QuestionsOut)
async def list_questions(
    session_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> QuestionsOut:
    if not user_can_use_session(db, user, session_id):
        raise HTTPException(status_code=403, detail="Not allowed for this session")
    qs = session_store.load_questions_public(session_id)
    if not qs:
        raise HTTPException(status_code=404, detail="No generated questions yet.")

    deadline_utc: datetime | None = None
    limit_minutes: int | None = None
    row = _trainee_assignment_row(db, user, session_id)
    if row is not None:
        deadline_utc, limit_minutes = _apply_trainee_attempt_window(db, row)

    return QuestionsOut(
        questions=qs,
        assessment_deadline_utc=deadline_utc,
        assessment_time_limit_minutes=limit_minutes,
    )


@router.post("/{session_id}/evaluate", response_model=EvaluateResponse)
async def evaluate(
    session_id: str,
    body: EvaluateBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> EvaluateResponse:
    if not user_can_use_session(db, user, session_id):
        raise HTTPException(status_code=403, detail="Not allowed for this session")

    assignment_row: Assignment | None = None
    if user.role == "user":
        assignment_row = (
            db.query(Assignment)
            .filter(Assignment.session_id == session_id, Assignment.assigned_user_id == user.id)
            .first()
        )
        if assignment_row is None:
            raise HTTPException(status_code=403, detail="No assignment for this session")
        if assignment_row.submitted_at is not None:
            raise HTTPException(status_code=400, detail="Assessment already submitted for this assignment")
        _apply_trainee_attempt_window(db, assignment_row)

    pub = session_store.load_questions_public(session_id)
    prv = session_store.load_questions_private(session_id)

    if not pub or not prv:
        raise HTTPException(status_code=404, detail="Questions not generated for evaluation.")

    session_dir = session_store.session_dir(session_id)
    idx = load_index_optional(session_dir)

    answers_map = {a.question_id: a.answer for a in body.answers}
    try:
        result = evaluate_answers(idx, pub, prv, answers_map)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {exc}") from exc

    if assignment_row is not None:
        assignment_row.evaluation_json = result.model_dump_json()
        assignment_row.submitted_at = datetime.now(timezone.utc)
        db.add(assignment_row)
        db.commit()

    return result
