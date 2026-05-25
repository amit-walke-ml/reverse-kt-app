from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Local/dev default; matches AssessmentApp/docker-compose Postgres service (user kt, db kt_assessment).
DEFAULT_POSTGRES_URL = (
    "postgresql+psycopg://kt:kt_app_local_change_me@localhost:5432/kt_assessment"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Reverse KT Assessment API"
    api_v1_prefix: str = "/api/v1"
    data_dir: Path = Path(__file__).resolve().parent.parent.parent / "data"

    # OpenAI or Azure OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-02-15-preview"
    azure_openai_chat_deployment: str = ""
    azure_openai_embedding_deployment: str = ""

    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://127.0.0.1:8000,http://localhost:8000,"
        "null"
    )

    # RAG
    rag_top_k: int = 8
    chunk_target_chars: int = 1400
    chunk_overlap_chars: int = 160

    max_upload_mb: int = 50

    # Assessment generation (admin) and trainee attempt window
    assessment_question_count: int = Field(default=25, ge=3, le=100)
    assessment_time_limit_minutes: int = Field(default=60, ge=1, le=24 * 60)

    # Auth (JWT issued on login). Change JWT_SECRET_KEY in production.
    jwt_secret_key: str = "change-me-reverse-kt-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 10080  # 7 days

    default_admin_username: str = "admin"
    default_admin_password: str = "ReverseKT@123"
    # Local dev: reset admin password to default when hash does not match (e.g. stale volume).
    default_admin_reseed: bool = False

    # Default: Postgres at localhost (`docker compose up --build` starts db + API). Use "sqlite"
    # for file DB at DATA_DIR/kt_auth.db, or any SQLAlchemy URL.
    database_url_override: str = Field(default=DEFAULT_POSTGRES_URL)

    # Static UI: unset = auto-detect (repo vs Docker). Override with STATIC_FRONTEND_DIR env.
    static_frontend_dir: Optional[Path] = Field(default=None)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def has_llm_credentials(self) -> bool:
        if self.azure_openai_endpoint.strip() and self.azure_openai_api_key.strip():
            return True
        return bool(self.openai_api_key.strip())

    @property
    def sqlalchemy_database_uri(self) -> str:
        o = (self.database_url_override or "").strip()
        if not o:
            return DEFAULT_POSTGRES_URL
        if o.lower() == "sqlite":
            self.data_dir.mkdir(parents=True, exist_ok=True)
            p = (self.data_dir / "kt_auth.db").resolve()
            return f"sqlite:///{p.as_posix()}"
        return o


settings = Settings()
