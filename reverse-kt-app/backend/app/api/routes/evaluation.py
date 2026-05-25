"""Evaluation endpoints — Answer submission and results."""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.models.schemas import (
    EvaluateRequest,
    EvaluationResult,
    AssessmentSet,
)
from app.services.evaluator import Evaluator
from app.api.routes.upload import sessions, _get_session_dir
from app.api.routes.assessment import session_assessments

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assessment", tags=["evaluation"])

# Store evaluation results
session_results: dict[str, EvaluationResult] = {}


@router.post("/{session_id}/evaluate")
async def evaluate_answers(session_id: str, request: EvaluateRequest):
    """Submit answers for evaluation and receive results."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get assessment
    assessment = session_assessments.get(session_id)
    if not assessment:
        # Try loading from disk
        session_dir = _get_session_dir(session_id)
        assessment_path = session_dir / "assessment.json"
        if assessment_path.exists():
            with open(assessment_path, "r", encoding="utf-8") as f:
                assessment = AssessmentSet(**json.load(f))
                session_assessments[session_id] = assessment
        else:
            raise HTTPException(status_code=404, detail="No assessment found. Generate questions first.")

    if not request.answers:
        raise HTTPException(status_code=400, detail="No answers provided")

    try:
        evaluator = Evaluator()
        result = evaluator.evaluate(
            assessment=assessment,
            answers=request.answers,
            session_id=session_id,
        )

        # Store results
        session_results[session_id] = result
        session.evaluated = True

        # Save to disk
        session_dir = _get_session_dir(session_id)
        results_path = session_dir / "results.json"
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(), f, indent=2, default=str)

        return result.model_dump()

    except Exception as e:
        logger.exception(f"Evaluation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(e)}")


@router.get("/{session_id}/results")
async def get_results(session_id: str):
    """Retrieve evaluation results for a session."""
    result = session_results.get(session_id)

    if not result:
        # Try loading from disk
        session_dir = _get_session_dir(session_id)
        results_path = session_dir / "results.json"
        if results_path.exists():
            with open(results_path, "r", encoding="utf-8") as f:
                result = EvaluationResult(**json.load(f))
                session_results[session_id] = result
        else:
            raise HTTPException(status_code=404, detail="No evaluation results found")

    return result.model_dump()
