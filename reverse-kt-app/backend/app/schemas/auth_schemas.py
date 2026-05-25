"""Pydantic schemas for auth / admin APIs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserPublic(BaseModel):
    id: int
    username: str
    full_name: str | None
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


class UserCreateAdmin(BaseModel):
    username: str = Field(min_length=2, max_length=128)
    password: str = Field(min_length=6, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class UserUpdateAdmin(BaseModel):
    """Partial update for user management (admin only). Omit fields to leave unchanged."""

    username: str | None = Field(default=None, min_length=2, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=6, max_length=128)
    is_active: bool | None = None


class AssignmentCreate(BaseModel):
    session_id: str
    assigned_user_id: int
    title: str | None = Field(default=None, max_length=512)


class AssignmentOut(BaseModel):
    id: int
    session_id: str
    assigned_user_id: int
    title: str | None
    submitted_at: datetime | None
    has_result: bool

    model_config = {"from_attributes": True}


class AssignmentDetailOut(AssignmentOut):
    evaluation: dict | None = None


class AdminResultRow(BaseModel):
    assignment_id: int
    session_id: str
    trainee_username: str
    trainee_full_name: str | None
    submitted_at: datetime | None
    percentage: float | None
    readiness_level: str | None
    evaluation: dict
