"""Create database tables and seed default admin on startup."""

from __future__ import annotations

# Serialize bootstrap across Uvicorn workers (each runs lifespan + create_all).
_POSTGRES_BOOTSTRAP_LOCK_KEY = 847_362_591


def _ensure_assignments_attempt_column(engine) -> None:
    """Add assessment_started_at when upgrading an existing DB (create_all does not alter tables)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "assignments" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("assignments")}
    if "assessment_started_at" in cols:
        return
    ddl = (
        "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS assessment_started_at TIMESTAMP WITH TIME ZONE"
        if engine.dialect.name == "postgresql"
        else "ALTER TABLE assignments ADD COLUMN assessment_started_at DATETIME"
    )
    with engine.begin() as conn:
        conn.execute(text(ddl))


def init_database() -> None:
    import app.models.user_models  # noqa: F401 - register ORM metadata
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.core.config import settings
    from app.core.security import hash_password
    from app.db.database import Base, SessionLocal, engine
    from app.models.user_models import User

    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": _POSTGRES_BOOTSTRAP_LOCK_KEY},
            )
            Base.metadata.create_all(bind=conn)
            with Session(bind=conn) as session:
                exists = (
                    session.query(User)
                    .filter(User.username == settings.default_admin_username)
                    .first()
                )
                if exists is None:
                    session.add(
                        User(
                            username=settings.default_admin_username,
                            hashed_password=hash_password(settings.default_admin_password),
                            full_name="Administrator",
                            role="admin",
                        )
                    )
        _ensure_assignments_attempt_column(engine)
        return

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        exists = db.query(User).filter(User.username == settings.default_admin_username).first()
        if exists is None:
            db.add(
                User(
                    username=settings.default_admin_username,
                    hashed_password=hash_password(settings.default_admin_password),
                    full_name="Administrator",
                    role="admin",
                )
            )
            db.commit()
    finally:
        db.close()

    _ensure_assignments_attempt_column(engine)
