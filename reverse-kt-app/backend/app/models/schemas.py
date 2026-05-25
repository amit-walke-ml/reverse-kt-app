"""Pydantic models and DTOs for the KT Assessment System."""

from __future__ import annotations
from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional
from datetime import datetime


# ── Enums ─────────────────────────────────────────────────────────────

class SourceType(str, Enum):
    PDF = "pdf"
    TRANSCRIPT = "transcript"


class QuestionType(str, Enum):
    MCQ = "mcq"
    SUBJECTIVE = "subjective"
    PRACTICAL = "practical"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    EXTRACTING_KNOWLEDGE = "extracting_knowledge"
    BUILDING_INDEX = "building_index"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Document Models ───────────────────────────────────────────────────

class DocumentSection(BaseModel):
    """A structured section extracted from a PDF."""
    section_type: str = Field(..., description="heading, paragraph, list, table, code")
    content: str
    level: int = Field(0, description="Heading level (1-6) or 0 for body")
    page_number: int = 0
    metadata: dict = Field(default_factory=dict)


class SpeakerTurn(BaseModel):
    """A single speaker turn from a transcript."""
    speaker: str = "Unknown"
    text: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None


class TranscriptSegment(BaseModel):
    """A topical segment of a transcript."""
    topic: str
    summary: str
    turns: list[SpeakerTurn] = []
    key_points: list[str] = []
    decisions: list[str] = []
    questions_raised: list[str] = []


class TranscriptDocument(BaseModel):
    """Processed transcript document."""
    speakers: list[str] = []
    segments: list[TranscriptSegment] = []
    full_clean_text: str = ""
    metadata: dict = Field(default_factory=dict)


# ── Chunk & Knowledge Models ─────────────────────────────────────────

class TextChunk(BaseModel):
    """A semantically meaningful text chunk ready for embedding."""
    chunk_id: str
    content: str
    source_type: SourceType
    source_file: str = ""
    section_title: str = ""
    page_or_timestamp: str = ""
    speaker: str = ""
    token_count: int = 0
    metadata: dict = Field(default_factory=dict)


class KnowledgeUnit(BaseModel):
    """Structured knowledge extracted from content."""
    topic: str
    concepts: list[str] = []
    steps: list[str] = []
    tools: list[str] = []
    decisions: list[str] = []
    common_issues: list[str] = []
    key_insights: list[str] = []
    source_type: SourceType = SourceType.PDF
    source_references: list[str] = []


class ExtractedKnowledge(BaseModel):
    """All extracted knowledge for a session."""
    session_id: str
    units: list[KnowledgeUnit] = []
    summary: str = ""
    total_concepts: int = 0
    total_from_pdf: int = 0
    total_from_transcript: int = 0


# ── Question Models ──────────────────────────────────────────────────

class MCQOption(BaseModel):
    """A single MCQ option."""
    label: str = Field(..., description="A, B, C, or D")
    text: str


class Question(BaseModel):
    """A generated assessment question."""
    question_id: int
    question_type: QuestionType
    difficulty: Difficulty = Difficulty.MEDIUM
    question_text: str
    context_hint: str = Field("", description="Brief context about where this comes from")
    source_type: SourceType = SourceType.PDF

    # MCQ-specific
    options: list[MCQOption] = []
    correct_option: str = ""

    # Expected answer / rubric
    expected_answer: str = ""
    grading_rubric: str = ""

    # For practical questions
    task_description: str = ""
    deliverables: list[str] = []


class AssessmentSet(BaseModel):
    """A complete set of 25 generated questions."""
    session_id: str
    questions: list[Question] = []
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    coverage_report: dict = Field(default_factory=dict)


# ── Evaluation Models ────────────────────────────────────────────────

class UserAnswer(BaseModel):
    """A user's answer to a single question."""
    question_id: int
    answer: str


class QuestionEvaluation(BaseModel):
    """Evaluation result for a single question."""
    question_id: int
    question_type: QuestionType
    score: float = Field(..., ge=0, le=10)
    max_score: float = 10.0
    is_correct: bool = False
    correct_answer: str = ""
    explanation: str = ""
    improvement_suggestions: str = ""
    key_concepts_covered: list[str] = []
    key_concepts_missed: list[str] = []


class EvaluationResult(BaseModel):
    """Complete evaluation results for an assessment."""
    session_id: str
    evaluations: list[QuestionEvaluation] = []
    total_score: float = 0.0
    max_total_score: float = 250.0
    percentage: float = 0.0
    mcq_score: float = 0.0
    subjective_score: float = 0.0
    practical_score: float = 0.0
    readiness_level: str = ""
    weak_areas: list[str] = []
    strong_areas: list[str] = []
    overall_feedback: str = ""
    evaluated_at: datetime = Field(default_factory=datetime.utcnow)


# ── Session Models ───────────────────────────────────────────────────

class SessionInfo(BaseModel):
    """Information about an assessment session."""
    session_id: str
    status: ProcessingStatus = ProcessingStatus.PENDING
    files_uploaded: list[str] = []
    source_types: list[SourceType] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    error_message: str = ""
    knowledge_summary: str = ""
    questions_generated: bool = False
    evaluated: bool = False


# ── API Request/Response Models ───────────────────────────────────────

class UploadResponse(BaseModel):
    session_id: str
    message: str
    files_received: list[str]
    status: ProcessingStatus


class StatusResponse(BaseModel):
    session_id: str
    status: ProcessingStatus
    message: str = ""
    details: dict = Field(default_factory=dict)


class GenerateRequest(BaseModel):
    difficulty_mix: Optional[str] = Field(
        "balanced",
        description="'balanced' (default), 'easy', 'medium', 'hard'"
    )


class EvaluateRequest(BaseModel):
    answers: list[UserAnswer]
