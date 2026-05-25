"""Dependencies: DB sessions, JWT user, RBAC."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.database import SessionLocal
from app.models.user_models import Assignment, SessionOwnership, User
from app.services.session_store import session_store


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_http_bearer = HTTPBearer(auto_error=False)


def _user_from_token_payload(db: Session, payload: dict) -> User | None:
    uid = payload.get("uid")
    if uid is None:
        return None
    return db.query(User).filter(User.id == int(uid), User.is_active.is_(True)).first()


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_http_bearer)],
) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(creds.credentials)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc
    user = _user_from_token_payload(db, payload)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or missing")
    return user


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


def require_user_or_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role not in ("admin", "user"):
        raise HTTPException(status_code=403, detail="Forbidden")
    return user


def user_can_use_session(db: Session, user: User, session_id: str) -> bool:
    if not session_store.session_dir(session_id).exists():
        return False
    if user.role == "admin":
        own = db.query(SessionOwnership).filter(SessionOwnership.session_id == session_id).first()
        return own is not None and own.owner_admin_id == user.id
    return (
        db.query(Assignment)
        .filter(Assignment.session_id == session_id, Assignment.assigned_user_id == user.id)
        .first()
        is not None
    )
