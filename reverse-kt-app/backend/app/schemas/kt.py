from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ProcessingStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    extracting_knowledge = "extracting_knowledge"
    building_index = "building_index"
    completed = "completed"
    failed = "failed"


class KnowledgeUnitOut(BaseModel):
    topic: str
    source_type: Literal["pdf", "transcript"]
    concepts: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    common_issues: list[str] = Field(default_factory=list)
    key_insights: list[str] = Field(default_factory=list)


class KnowledgePayload(BaseModel):
    summary: str = ""
    units: list[KnowledgeUnitOut] = Field(default_factory=list)


class UploadStatusOut(BaseModel):
    session_id: str
    status: ProcessingStatus
    message: str | None = None


class UploadResponse(BaseModel):
    session_id: str
    status: ProcessingStatus


class McqOptionOut(BaseModel):
    label: str
    text: str


class QuestionPublic(BaseModel):
    question_id: int
    question_type: Literal["mcq", "subjective", "practical"]
    question_text: str
    difficulty: str = "medium"
    context_hint: str | None = None
    options: list[McqOptionOut] | None = None
    task_description: str | None = None
    deliverables: list[str] | None = None


class QuestionsOut(BaseModel):
    questions: list[QuestionPublic]
    # Trainee in-progress attempt: server clock for UI + enforcement; null for admins or after submit.
    assessment_deadline_utc: datetime | None = None
    assessment_time_limit_minutes: int | None = None


class QuestionPrivate(BaseModel):
    question_id: int
    question_type: Literal["mcq", "subjective", "practical"]
    correct_option: str | None = None
    reference_answer: str = ""
    rubric_points: list[str] = Field(default_factory=list)
    rag_chunk_ids: list[str] = Field(default_factory=list)


class GenerateBody(BaseModel):
    difficulty_mix: str = "balanced"


class AnswerItem(BaseModel):
    question_id: int
    answer: str


class EvaluateBody(BaseModel):
    answers: list[AnswerItem]


class SingleEvaluationOut(BaseModel):
    question_id: int
    score: float
    correct_answer: str
    explanation: str
    improvement_suggestions: str = ""


class EvaluateResponse(BaseModel):
    total_score: float
    max_total_score: float = 250.0
    percentage: float
    mcq_score: float
    subjective_score: float
    practical_score: float
    readiness_level: str
    overall_feedback: str
    strong_areas: list[str]
    weak_areas: list[str]
    evaluations: list[SingleEvaluationOut]


class ChunkRecord(BaseModel):
    id: str
    text: str
    source: Literal["pdf", "transcript"]
    title: str = ""
    section_path: str = ""
