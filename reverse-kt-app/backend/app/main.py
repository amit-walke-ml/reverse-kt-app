from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router as api_v1_router
from app.core.config import settings

_startup_log = logging.getLogger("kt.startup")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.bootstrap import init_database

    init_database()
    if not settings.has_llm_credentials:
        _startup_log.warning(
            "No LLM credentials: set OPENAI_API_KEY or both AZURE_OPENAI_ENDPOINT and "
            "AZURE_OPENAI_API_KEY. Empty OPENAI_API_KEY in Docker (e.g. missing .env) causes "
            "pipeline failure at chat/embeddings—not a network outage."
        )
    yield




def _resolve_frontend_directory() -> Path | None:
    """Find the bundled HTML UI.

    Layouts:
    - Local repo: AssessmentApp/backend/app/main.py -> parents[...]/AssessmentApp/frontend
    - Docker: /app/app/main.py -> /app/frontend (sibling of package root)
    """
    if settings.static_frontend_dir is not None:
        p = Path(settings.static_frontend_dir)
        return p if p.is_dir() else None

    here = Path(__file__).resolve().parent  # .../app
    candidates = (
        here.parent.parent / "frontend",  # .../AssessmentApp/frontend (run from backend/)
        here.parent / "frontend",  # .../frontend next to Python package (/app/frontend in Docker)
    )
    for c in candidates:
        if c.is_dir():
            return c
    return None


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router, prefix=settings.api_v1_prefix)

_frontend = _resolve_frontend_directory()
if _frontend is not None:
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
