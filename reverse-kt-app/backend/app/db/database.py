from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


_eng_kw: dict = {"pool_pre_ping": True}
if settings.sqlalchemy_database_uri.startswith("sqlite"):
    _eng_kw["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.sqlalchemy_database_uri, **_eng_kw)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
