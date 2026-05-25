"""Admin-only endpoints: users, assignments, global results."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin
from app.core.security import hash_password
from app.models.user_models import Assignment, SessionOwnership, User
from app.schemas.auth_schemas import (
    AdminResultRow,
    AssignmentCreate,
    AssignmentOut,
    UserCreateAdmin,
    UserPublic,
    UserUpdateAdmin,
)

router = APIRouter(prefix="/admin", tags=["admin"])

AdminUser = Annotated[User, Depends(require_admin)]


@router.post("/users", response_model=UserPublic)
def create_regular_user(body: UserCreateAdmin, _admin: AdminUser, db: Session = Depends(get_db)) -> User:
    """Create a non-admin user (learners cannot self-register)."""
    uname = body.username.strip()
    if db.query(User).filter(User.username == uname).first():
        raise HTTPException(status_code=400, detail="Username already taken")
    u = User(
        username=uname,
        hashed_password=hash_password(body.password),
        full_name=body.full_name.strip() if body.full_name else None,
        role="user",
    )
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Username already taken") from None
    db.refresh(u)
    return u


@router.get("/users", response_model=list[UserPublic])
def list_users(_admin: AdminUser, db: Session = Depends(get_db)) -> list[User]:
    return db.query(User).order_by(User.id).all()


@router.patch("/users/{user_id}", response_model=UserPublic)
def update_user(
    user_id: int,
    body: UserUpdateAdmin,
    admin: AdminUser,
    db: Session = Depends(get_db),
) -> User:
    if not body.model_dump(exclude_unset=True):
        raise HTTPException(status_code=400, detail="Provide at least one field to update")

    row = db.query(User).filter(User.id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    if body.username is not None:
        uname = body.username.strip()
        conflict = db.query(User).filter(User.username == uname, User.id != user_id).first()
        if conflict:
            raise HTTPException(status_code=400, detail="Username already taken")
        row.username = uname

    if body.full_name is not None:
        row.full_name = body.full_name.strip() or None

    if body.password is not None:
        row.hashed_password = hash_password(body.password)

    if body.is_active is not None:
        if row.id == admin.id and not body.is_active:
            raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
        row.is_active = body.is_active

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Username already taken") from None
    db.refresh(row)
    return row


@router.delete("/users/{user_id}", status_code=204, response_class=Response)
def delete_user(user_id: int, admin: AdminUser, db: Session = Depends(get_db)) -> Response:
    row = db.query(User).filter(User.id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if row.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    if row.role == "admin":
        raise HTTPException(status_code=400, detail="Admin accounts cannot be deleted via API")

    db.query(Assignment).filter(Assignment.assigned_user_id == user_id).delete()
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.post("/assignments", response_model=AssignmentOut)
def create_assignment(body: AssignmentCreate, admin: AdminUser, db: Session = Depends(get_db)) -> AssignmentOut:
    assignee = db.query(User).filter(User.id == body.assigned_user_id).first()
    if not assignee or assignee.role != "user":
        raise HTTPException(status_code=400, detail="Target must be an active trainee user id")
    own = db.query(SessionOwnership).filter(SessionOwnership.session_id == body.session_id).first()
    if not own or own.owner_admin_id != admin.id:
        raise HTTPException(
            status_code=404, detail="Unknown KT session for this admin — upload and process materials first."
        )

    existing = (
        db.query(Assignment)
        .filter(
            Assignment.session_id == body.session_id,
            Assignment.assigned_user_id == body.assigned_user_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="User already assigned to this session")

    row = Assignment(
        session_id=body.session_id,
        assigned_user_id=body.assigned_user_id,
        assigned_by_id=admin.id,
        title=body.title,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="User already assigned to this session (or duplicate record).",
        ) from None
    db.refresh(row)
    return AssignmentOut(
        id=row.id,
        session_id=row.session_id,
        assigned_user_id=row.assigned_user_id,
        title=row.title,
        submitted_at=row.submitted_at,
        has_result=row.evaluation_json is not None,
    )


@router.get("/results", response_model=list[AdminResultRow])
def all_submissions(_admin: AdminUser, db: Session = Depends(get_db)) -> list[AdminResultRow]:
    rows = db.query(Assignment).filter(Assignment.evaluation_json.isnot(None)).order_by(Assignment.id).all()
    out: list[AdminResultRow] = []
    for a in rows:
        assignee = db.query(User).filter(User.id == a.assigned_user_id).first()
        ev = json.loads(a.evaluation_json or "{}")
        out.append(
            AdminResultRow(
                assignment_id=a.id,
                session_id=a.session_id,
                trainee_username=assignee.username if assignee else "?",
                trainee_full_name=assignee.full_name if assignee else None,
                submitted_at=a.submitted_at,
                percentage=ev.get("percentage"),
                readiness_level=ev.get("readiness_level"),
                evaluation=ev,
            )
        )
    return out
