"""Assessment endpoints — Question generation and retrieval."""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.models.schemas import (
    GenerateRequest,
    AssessmentSet,
    ProcessingStatus,
    ExtractedKnowledge,
)
from app.services.question_generator import QuestionGenerator
from app.services.vector_store import VectorStore
from app.api.routes.upload import sessions, session_knowledge, session_vector_stores, _get_session_dir

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assessment", tags=["assessment"])

# Store generated assessments
session_assessments: dict[str, AssessmentSet] = {}


@router.post("/{session_id}/generate")
async def generate_questions(session_id: str, request: GenerateRequest = None):
    """Generate 25 assessment questions for a processed session."""
    if request is None:
        request = GenerateRequest()

    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status != ProcessingStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Processing not complete. Status: {session.status.value}"
        )

    # Get knowledge
    knowledge = session_knowledge.get(session_id)
    if not knowledge:
        session_dir = _get_session_dir(session_id)
        knowledge_path = session_dir / "knowledge.json"
        if knowledge_path.exists():
            with open(knowledge_path, "r", encoding="utf-8") as f:
                knowledge = ExtractedKnowledge(**json.load(f))
                session_knowledge[session_id] = knowledge
        else:
            raise HTTPException(status_code=404, detail="Knowledge not found. Re-upload files.")

    # Get or load vector store
    vector_store = session_vector_stores.get(session_id)
    if not vector_store:
        vector_store = VectorStore()
        if not vector_store.load_index(session_id):
            raise HTTPException(status_code=404, detail="Vector index not found. Re-upload files.")
        session_vector_stores[session_id] = vector_store

    # Generate questions
    try:
        generator = QuestionGenerator(vector_store)
        assessment = generator.generate(
            knowledge=knowledge,
            session_id=session_id,
            difficulty_mix=request.difficulty_mix or "balanced",
        )

        # Store assessment
        session_assessments[session_id] = assessment
        session.questions_generated = True

        # Save to disk
        session_dir = _get_session_dir(session_id)
        assessment_path = session_dir / "assessment.json"
        with open(assessment_path, "w", encoding="utf-8") as f:
            json.dump(assessment.model_dump(), f, indent=2, default=str)

        return assessment.model_dump()

    except Exception as e:
        logger.exception(f"Question generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Question generation failed: {str(e)}")


@router.get("/{session_id}/questions")
async def get_questions(session_id: str):
    """Retrieve generated questions for a session."""
    assessment = session_assessments.get(session_id)

    if not assessment:
        # Try loading from disk
        session_dir = _get_session_dir(session_id)
        assessment_path = session_dir / "assessment.json"
        if assessment_path.exists():
            with open(assessment_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                assessment = AssessmentSet(**data)
                session_assessments[session_id] = assessment
        else:
            raise HTTPException(status_code=404, detail="No questions generated yet")

    # Return questions without expected answers (for quiz mode)
    questions_for_user = []
    for q in assessment.questions:
        q_dict = q.model_dump()
        # Remove answer-related fields for the quiz taker
        q_dict.pop("expected_answer", None)
        q_dict.pop("grading_rubric", None)
        if q.question_type.value == "mcq":
            q_dict.pop("correct_option", None)
        questions_for_user.append(q_dict)

    return {
        "session_id": session_id,
        "total_questions": len(questions_for_user),
        "questions": questions_for_user,
        "coverage_report": assessment.coverage_report,
    }
