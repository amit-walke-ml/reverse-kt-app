"""Create database tables and seed default admin on startup."""

from __future__ import annotations

import logging
import time

from sqlalchemy.exc import OperationalError

# Serialize bootstrap across Uvicorn workers (each runs lifespan + create_all).
_POSTGRES_BOOTSTRAP_LOCK_KEY = 847_362_591
_BOOTSTRAP_LOG = logging.getLogger("kt.bootstrap")
_DB_CONNECT_ATTEMPTS = 30
_DB_CONNECT_DELAY_SEC = 1.0


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


def _ensure_users_is_active_column(engine) -> None:
    """Backfill is_active for DBs created before server_default existed."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "is_active" not in cols:
        return
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET is_active = TRUE WHERE is_active IS NULL"))


def _seed_default_admin(session) -> None:
    from app.core.config import settings
    from app.core.security import hash_password, verify_password
    from app.models.user_models import User

    user = (
        session.query(User)
        .filter(User.username == settings.default_admin_username)
        .first()
    )
    if user is None:
        session.add(
            User(
                username=settings.default_admin_username,
                hashed_password=hash_password(settings.default_admin_password),
                full_name="Administrator",
                role="admin",
                is_active=True,
            )
        )
        _BOOTSTRAP_LOG.info(
            "Created default admin user %r",
            settings.default_admin_username,
        )
        return

    if user.is_active is False or user.is_active is None:
        user.is_active = True

    if settings.default_admin_reseed and not verify_password(
        settings.default_admin_password, user.hashed_password
    ):
        user.hashed_password = hash_password(settings.default_admin_password)
        user.role = "admin"
        user.is_active = True
        _BOOTSTRAP_LOG.warning(
            "Reset password for default admin %r (DEFAULT_ADMIN_RESEED=true)",
            settings.default_admin_username,
        )


def _init_database_once() -> None:
    import app.models.user_models  # noqa: F401 - register ORM metadata
    from sqlalchemy import text

    from app.core.config import settings
    from app.db.database import Base, SessionLocal, engine

    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": _POSTGRES_BOOTSTRAP_LOCK_KEY},
            )
            Base.metadata.create_all(bind=conn)

        with SessionLocal() as session:
            with session.begin():
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": _POSTGRES_BOOTSTRAP_LOCK_KEY},
                )
                _seed_default_admin(session)
        _ensure_users_is_active_column(engine)
        _ensure_assignments_attempt_column(engine)
        return

    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        with session.begin():
            _seed_default_admin(session)

    _ensure_users_is_active_column(engine)
    _ensure_assignments_attempt_column(engine)


def init_database() -> None:
    last_err: OperationalError | None = None
    for attempt in range(1, _DB_CONNECT_ATTEMPTS + 1):
        try:
            _init_database_once()
            return
        except OperationalError as exc:
            last_err = exc
            if attempt >= _DB_CONNECT_ATTEMPTS:
                raise
            _BOOTSTRAP_LOG.warning(
                "Database not ready (attempt %s/%s): %s",
                attempt,
                _DB_CONNECT_ATTEMPTS,
                exc,
            )
            time.sleep(_DB_CONNECT_DELAY_SEC)
    if last_err is not None:
        raise last_err
