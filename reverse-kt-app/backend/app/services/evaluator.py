"""Evaluation Engine Service — LLM-based answer evaluation with rubric scoring.

Evaluates user responses for all question types:
- MCQ: Direct comparison + explanation
- Subjective: Rubric-based scoring (0-10)
- Practical: Approach/methodology evaluation

Returns detailed feedback with improvement suggestions.
"""

import json
import logging
from typing import Optional

from openai import OpenAI

from app.config import get_settings
from app.models.schemas import (
    Question,
    QuestionType,
    UserAnswer,
    QuestionEvaluation,
    EvaluationResult,
    AssessmentSet,
)
from app.prompts.evaluation import (
    MCQ_EVALUATION_SYSTEM,
    MCQ_EVALUATION_USER,
    SUBJECTIVE_EVALUATION_SYSTEM,
    SUBJECTIVE_EVALUATION_USER,
    PRACTICAL_EVALUATION_SYSTEM,
    PRACTICAL_EVALUATION_USER,
    OVERALL_EVALUATION_SYSTEM,
    OVERALL_EVALUATION_USER,
)
from app.utils.helpers import safe_json_parse

logger = logging.getLogger(__name__)


class Evaluator:
    """Evaluate user answers using LLM-based assessment."""

    def __init__(self):
        settings = get_settings()
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL

    def evaluate(
        self,
        assessment: AssessmentSet,
        answers: list[UserAnswer],
        session_id: str,
    ) -> EvaluationResult:
        """Evaluate all user answers and produce comprehensive results."""
        logger.info(
            f"Evaluating {len(answers)} answers for session {session_id}"
        )

        # Build question lookup
        question_map = {q.question_id: q for q in assessment.questions}

        evaluations = []
        for answer in answers:
            question = question_map.get(answer.question_id)
            if not question:
                logger.warning(f"Question {answer.question_id} not found")
                continue

            try:
                evaluation = self._evaluate_single(question, answer)
                evaluations.append(evaluation)
            except Exception as e:
                logger.error(
                    f"Failed to evaluate question {answer.question_id}: {e}"
                )
                evaluations.append(
                    QuestionEvaluation(
                        question_id=answer.question_id,
                        question_type=question.question_type,
                        score=0,
                        is_correct=False,
                        correct_answer=question.expected_answer,
                        explanation="Evaluation failed. Please review manually.",
                        improvement_suggestions="",
                    )
                )

        # Calculate scores
        total_score = sum(e.score for e in evaluations)
        max_total = len(evaluations) * 10
        percentage = (total_score / max_total * 100) if max_total > 0 else 0

        mcq_evals = [e for e in evaluations if e.question_type == QuestionType.MCQ]
        subj_evals = [
            e for e in evaluations if e.question_type == QuestionType.SUBJECTIVE
        ]
        prac_evals = [
            e for e in evaluations if e.question_type == QuestionType.PRACTICAL
        ]

        mcq_score = sum(e.score for e in mcq_evals)
        subjective_score = sum(e.score for e in subj_evals)
        practical_score = sum(e.score for e in prac_evals)

        # Get overall assessment
        overall = self._generate_overall_assessment(
            total_score, max_total, percentage,
            mcq_score, subjective_score, practical_score,
            evaluations
        )

        result = EvaluationResult(
            session_id=session_id,
            evaluations=evaluations,
            total_score=total_score,
            max_total_score=max_total,
            percentage=round(percentage, 1),
            mcq_score=mcq_score,
            subjective_score=subjective_score,
            practical_score=practical_score,
            readiness_level=overall.get("readiness_level", ""),
            weak_areas=overall.get("weak_areas", []),
            strong_areas=overall.get("strong_areas", []),
            overall_feedback=overall.get("overall_feedback", ""),
        )

        logger.info(
            f"Evaluation complete: {total_score}/{max_total} "
            f"({percentage:.1f}%) — {result.readiness_level}"
        )

        return result

    def _evaluate_single(
        self, question: Question, answer: UserAnswer
    ) -> QuestionEvaluation:
        """Evaluate a single question-answer pair."""
        if question.question_type == QuestionType.MCQ:
            return self._evaluate_mcq(question, answer)
        elif question.question_type == QuestionType.SUBJECTIVE:
            return self._evaluate_subjective(question, answer)
        elif question.question_type == QuestionType.PRACTICAL:
            return self._evaluate_practical(question, answer)
        else:
            raise ValueError(f"Unknown question type: {question.question_type}")

    def _evaluate_mcq(
        self, question: Question, answer: UserAnswer
    ) -> QuestionEvaluation:
        """Evaluate an MCQ answer."""
        options_text = "\n".join(
            f"{opt.label}) {opt.text}" for opt in question.options
        )

        user_prompt = MCQ_EVALUATION_USER.format(
            question_text=question.question_text,
            options_text=options_text,
            correct_option=question.correct_option,
            user_answer=answer.answer,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": MCQ_EVALUATION_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=800,
            response_format={"type": "json_object"},
        )

        data = safe_json_parse(response.choices[0].message.content)

        return QuestionEvaluation(
            question_id=question.question_id,
            question_type=QuestionType.MCQ,
            score=data.get("score", 0),
            is_correct=data.get("is_correct", False),
            correct_answer=data.get("correct_answer", question.correct_option),
            explanation=data.get("explanation", ""),
            improvement_suggestions=data.get("improvement_suggestions", ""),
        )

    def _evaluate_subjective(
        self, question: Question, answer: UserAnswer
    ) -> QuestionEvaluation:
        """Evaluate a subjective answer."""
        user_prompt = SUBJECTIVE_EVALUATION_USER.format(
            question_text=question.question_text,
            expected_answer=question.expected_answer,
            grading_rubric=question.grading_rubric,
            user_answer=answer.answer,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SUBJECTIVE_EVALUATION_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )

        data = safe_json_parse(response.choices[0].message.content)

        return QuestionEvaluation(
            question_id=question.question_id,
            question_type=QuestionType.SUBJECTIVE,
            score=min(float(data.get("score", 0)), 10),
            is_correct=data.get("is_correct", False),
            correct_answer=data.get("correct_answer", question.expected_answer),
            explanation=data.get("explanation", ""),
            improvement_suggestions=data.get("improvement_suggestions", ""),
            key_concepts_covered=data.get("key_concepts_covered", []),
            key_concepts_missed=data.get("key_concepts_missed", []),
        )

    def _evaluate_practical(
        self, question: Question, answer: UserAnswer
    ) -> QuestionEvaluation:
        """Evaluate a practical assignment answer."""
        user_prompt = PRACTICAL_EVALUATION_USER.format(
            question_text=question.question_text,
            task_description=question.task_description,
            expected_answer=question.expected_answer,
            grading_rubric=question.grading_rubric,
            deliverables=", ".join(question.deliverables),
            user_answer=answer.answer,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": PRACTICAL_EVALUATION_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=1200,
            response_format={"type": "json_object"},
        )

        data = safe_json_parse(response.choices[0].message.content)

        return QuestionEvaluation(
            question_id=question.question_id,
            question_type=QuestionType.PRACTICAL,
            score=min(float(data.get("score", 0)), 10),
            is_correct=data.get("is_correct", False),
            correct_answer=data.get("correct_answer", question.expected_answer),
            explanation=data.get("explanation", ""),
            improvement_suggestions=data.get("improvement_suggestions", ""),
            key_concepts_covered=data.get("key_concepts_covered", []),
            key_concepts_missed=data.get("key_concepts_missed", []),
        )

    def _generate_overall_assessment(
        self,
        total_score: float,
        max_score: float,
        percentage: float,
        mcq_score: float,
        subjective_score: float,
        practical_score: float,
        evaluations: list[QuestionEvaluation],
    ) -> dict:
        """Generate overall assessment summary."""
        # Build per-question breakdown
        breakdown_parts = []
        for e in evaluations:
            status = "✓" if e.is_correct else "✗"
            breakdown_parts.append(
                f"Q{e.question_id} ({e.question_type.value}): "
                f"{status} Score: {e.score}/10"
            )

        user_prompt = OVERALL_EVALUATION_USER.format(
            total_score=total_score,
            max_score=max_score,
            percentage=f"{percentage:.1f}",
            mcq_score=mcq_score,
            subjective_score=subjective_score,
            practical_score=practical_score,
            questions_breakdown="\n".join(breakdown_parts),
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": OVERALL_EVALUATION_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=1000,
                response_format={"type": "json_object"},
            )

            return safe_json_parse(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"Overall assessment generation failed: {e}")
            # Fallback
            if percentage >= 90:
                level = "Expert"
            elif percentage >= 75:
                level = "Proficient"
            elif percentage >= 60:
                level = "Developing"
            elif percentage >= 40:
                level = "Needs Improvement"
            else:
                level = "Insufficient"

            return {
                "readiness_level": level,
                "strong_areas": [],
                "weak_areas": [],
                "overall_feedback": f"Score: {percentage:.1f}%. Readiness level: {level}.",
            }
