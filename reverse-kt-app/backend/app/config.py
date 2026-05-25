"""Application configuration using Pydantic BaseSettings."""

import os
from pathlib import Path
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # App
    APP_NAME: str = "KT Assessment System"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBEDDING_DIMENSIONS: int = 1536

    # Processing
    CHUNK_SIZE_TOKENS: int = 500
    CHUNK_OVERLAP_TOKENS: int = 50
    MAX_CHUNKS_PER_QUERY: int = 8
    SIMILARITY_THRESHOLD: float = 0.3

    # Question Generation
    NUM_MCQ: int = 10
    NUM_SUBJECTIVE: int = 8
    NUM_PRACTICAL: int = 7
    TOTAL_QUESTIONS: int = 25

    # Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    UPLOAD_DIR: Path = DATA_DIR / "uploads"
    PROCESSED_DIR: Path = DATA_DIR / "processed"
    INDEX_DIR: Path = DATA_DIR / "indices"

    # CORS
    ALLOWED_ORIGINS: list[str] = ["*"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    def ensure_dirs(self):
        """Create necessary directories if they don't exist."""
        for d in [self.UPLOAD_DIR, self.PROCESSED_DIR, self.INDEX_DIR]:
            d.mkdir(parents=True, exist_ok=True)


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    settings = Settings()
    settings.ensure_dirs()
    return settings
