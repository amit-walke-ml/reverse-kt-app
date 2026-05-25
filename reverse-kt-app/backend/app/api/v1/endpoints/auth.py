from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.security import create_access_token, verify_password
from app.models.user_models import User
from app.schemas.auth_schemas import LoginRequest, TokenResponse, UserPublic

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.username == payload.username.strip()).first()
    if not user or user.is_active is False:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token(subject_username=user.username, user_id=user.id, role=user.role)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserPublic)
def read_me(user: User = Depends(get_current_user)) -> User:
    return user
