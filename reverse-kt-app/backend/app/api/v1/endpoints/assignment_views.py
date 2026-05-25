"""Trainee-facing assignment APIs."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user_models import Assignment, User
from app.schemas.auth_schemas import AssignmentDetailOut, AssignmentOut

router = APIRouter(prefix="/assignments", tags=["assignments"])

TraineeUser = Annotated[User, Depends(get_current_user)]


@router.get("/me", response_model=list[AssignmentOut])
def my_assignments(user: TraineeUser, db: Session = Depends(get_db)) -> list[AssignmentOut]:
    if user.role != "user":
        raise HTTPException(status_code=403, detail="Trainees only")
    rows = db.query(Assignment).filter(Assignment.assigned_user_id == user.id).order_by(Assignment.id).all()
    return [
        AssignmentOut(
            id=r.id,
            session_id=r.session_id,
            assigned_user_id=r.assigned_user_id,
            title=r.title,
            submitted_at=r.submitted_at,
            has_result=r.evaluation_json is not None,
        )
        for r in rows
    ]


@router.get("/me/{assignment_id}", response_model=AssignmentDetailOut)
def my_assignment_detail(assignment_id: int, user: TraineeUser, db: Session = Depends(get_db)) -> AssignmentDetailOut:
    if user.role != "user":
        raise HTTPException(status_code=403, detail="Trainees only")
    r = db.query(Assignment).filter(Assignment.id == assignment_id, Assignment.assigned_user_id == user.id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Assignment not found")
    ev = json.loads(r.evaluation_json) if r.evaluation_json else None
    return AssignmentDetailOut(
        id=r.id,
        session_id=r.session_id,
        assigned_user_id=r.assigned_user_id,
        title=r.title,
        submitted_at=r.submitted_at,
        has_result=r.evaluation_json is not None,
        evaluation=ev,
    )
