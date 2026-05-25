"""SQL persistence for users, session ownership, and assignment workflow."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32))  # admin | user
    is_active: Mapped[bool] = mapped_column(default=True)

    assignments_received: Mapped[list["Assignment"]] = relationship(
        "Assignment",
        foreign_keys="Assignment.assigned_user_id",
        back_populates="assignee",
    )


class SessionOwnership(Base):
    """Marks which admin created a KT session (filesystem session_id)."""

    __tablename__ = "session_ownership"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Assignment(Base):
    """Ties an existing KT filesystem session_id to an end user."""

    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("session_id", "assigned_user_id", name="uq_assignment_session_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    assigned_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assigned_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Trainee: set on first GET /assessment/{session}/questions; used with assessment_time_limit_minutes.
    assessment_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evaluation_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    assignee: Mapped["User"] = relationship(
        "User",
        foreign_keys=[assigned_user_id],
        back_populates="assignments_received",
    )
