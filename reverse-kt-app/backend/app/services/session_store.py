import json
import threading
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.schemas.kt import (
    ChunkRecord,
    KnowledgePayload,
    ProcessingStatus,
    QuestionPrivate,
    QuestionPublic,
)


class SessionStore:
    """Filesystem-backed session state (survives process restarts for same host)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        settings.data_dir.mkdir(parents=True, exist_ok=True)

    def new_session_id(self) -> str:
        return str(uuid.uuid4())

    def session_dir(self, session_id: str) -> Path:
        return settings.data_dir / "sessions" / session_id

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _read_json(self, path: Path) -> Any:
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def init_session(self, session_id: str) -> None:
        with self._lock:
            d = self.session_dir(session_id)
            d.mkdir(parents=True, exist_ok=True)
            self._write_json(
                d / "status.json",
                {"status": ProcessingStatus.pending.value, "message": None},
            )

    def set_status(self, session_id: str, status: ProcessingStatus, message: str | None = None) -> None:
        with self._lock:
            self._write_json(
                self.session_dir(session_id) / "status.json",
                {"status": status.value, "message": message},
            )

    def get_status(self, session_id: str) -> dict[str, Any]:
        data = self._read_json(self.session_dir(session_id) / "status.json")
        if not data:
            return {"status": ProcessingStatus.failed.value, "message": "Unknown session"}
        return data

    def save_raw_sources(self, session_id: str, pdf_text: str, transcript_text: str) -> None:
        with self._lock:
            d = self.session_dir(session_id)
            (d / "sources").mkdir(parents=True, exist_ok=True)
            (d / "sources" / "pdf_normalized.txt").write_text(pdf_text, encoding="utf-8")
            (d / "sources" / "transcript_normalized.txt").write_text(transcript_text, encoding="utf-8")

    def save_chunks(self, session_id: str, chunks: list[ChunkRecord]) -> None:
        with self._lock:
            self._write_json(
                self.session_dir(session_id) / "chunks.json",
                [c.model_dump() for c in chunks],
            )

    def load_chunks(self, session_id: str) -> list[ChunkRecord]:
        data = self._read_json(self.session_dir(session_id) / "chunks.json") or []
        return [ChunkRecord.model_validate(x) for x in data]

    def save_knowledge(self, session_id: str, knowledge: KnowledgePayload) -> None:
        with self._lock:
            self._write_json(self.session_dir(session_id) / "knowledge.json", knowledge.model_dump())

    def load_knowledge(self, session_id: str) -> KnowledgePayload | None:
        data = self._read_json(self.session_dir(session_id) / "knowledge.json")
        if not data:
            return None
        return KnowledgePayload.model_validate(data)

    def save_questions(
        self,
        session_id: str,
        public: list[QuestionPublic],
        private: list[QuestionPrivate],
    ) -> None:
        with self._lock:
            d = self.session_dir(session_id)
            self._write_json(d / "questions_public.json", [q.model_dump() for q in public])
            self._write_json(d / "questions_private.json", [q.model_dump() for q in private])

    def load_questions_public(self, session_id: str) -> list[QuestionPublic]:
        data = self._read_json(self.session_dir(session_id) / "questions_public.json") or []
        return [QuestionPublic.model_validate(x) for x in data]

    def load_questions_private(self, session_id: str) -> list[QuestionPrivate]:
        data = self._read_json(self.session_dir(session_id) / "questions_private.json") or []
        return [QuestionPrivate.model_validate(x) for x in data]

    def append_generation_record(self, session_id: str, generation_id: str, question_signature: str) -> None:
        path = self.session_dir(session_id) / "generation_history.json"
        with self._lock:
            payload = self._read_json(path) or {"generations": []}
            gens = list(payload.get("generations") or [])
            gens.append(
                {
                    "id": generation_id,
                    "signature": question_signature,
                }
            )
            payload["generations"] = gens[-48:]
            self._write_json(path, payload)

    def recent_generation_signatures(self, session_id: str, limit: int = 5) -> list[str]:
        path = self.session_dir(session_id) / "generation_history.json"
        data = self._read_json(path) or {}
        gens = list(data.get("generations") or [])
        out = [str(g.get("signature") or "").strip() for g in gens if str(g.get("signature") or "").strip()]
        return out[-limit:]

    def faiss_paths(self, session_id: str) -> tuple[Path, Path]:
        d = self.session_dir(session_id)
        return d / "faiss.index", d / "faiss_meta.json"


session_store = SessionStore()
