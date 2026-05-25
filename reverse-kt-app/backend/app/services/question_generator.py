"""Question Generator Service — RAG-based assessment question generation.

Generates exactly 25 questions per assessment:
- 10 MCQs (concept + scenario based)
- 8 Subjective (why/how, explanation-based)
- 7 Practical (hands-on assignments)

Uses retrieved context from FAISS + structured knowledge for grounded generation.
"""

import json
import logging
from typing import Optional

from openai import OpenAI

from app.config import get_settings
from app.models.schemas import (
    Question,
    QuestionType,
    Difficulty,
    MCQOption,
    AssessmentSet,
    ExtractedKnowledge,
    SourceType,
)
from app.services.vector_store import VectorStore
from app.prompts.question_generation import (
    MCQ_GENERATION_SYSTEM,
    MCQ_GENERATION_USER,
    SUBJECTIVE_GENERATION_SYSTEM,
    SUBJECTIVE_GENERATION_USER,
    PRACTICAL_GENERATION_SYSTEM,
    PRACTICAL_GENERATION_USER,
)
from app.utils.helpers import safe_json_parse

logger = logging.getLogger(__name__)


class QuestionGenerator:
    """Generate assessment questions using RAG retrieval + LLM generation."""

    def __init__(self, vector_store: VectorStore):
        settings = get_settings()
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL
        self.vector_store = vector_store
        self.num_mcq = settings.NUM_MCQ
        self.num_subjective = settings.NUM_SUBJECTIVE
        self.num_practical = settings.NUM_PRACTICAL

    def generate(
        self,
        knowledge: ExtractedKnowledge,
        session_id: str,
        difficulty_mix: str = "balanced",
    ) -> AssessmentSet:
        """Generate a complete set of 25 assessment questions."""
        logger.info(f"Generating assessment for session {session_id}")

        # Extract main topics for retrieval
        topics = self._extract_topics(knowledge)
        logger.info(f"Identified {len(topics)} topics for question generation")

        # Get RAG context
        context = self.vector_store.get_context_for_questions(topics)
        knowledge_summary = self._build_knowledge_summary(knowledge)

        # Determine difficulty distribution
        difficulty_dist = self._get_difficulty_distribution(difficulty_mix)

        # Generate each question type
        questions = []
        q_id = 1

        # 1. Generate MCQs
        logger.info(f"Generating {self.num_mcq} MCQ questions...")
        mcq_questions = self._generate_mcqs(
            context, knowledge_summary, difficulty_dist, self.num_mcq
        )
        for q in mcq_questions:
            q.question_id = q_id
            q_id += 1
        questions.extend(mcq_questions)

        # 2. Generate Subjective
        logger.info(f"Generating {self.num_subjective} subjective questions...")
        subj_questions = self._generate_subjective(
            context, knowledge_summary, difficulty_dist, self.num_subjective
        )
        for q in subj_questions:
            q.question_id = q_id
            q_id += 1
        questions.extend(subj_questions)

        # 3. Generate Practical
        logger.info(f"Generating {self.num_practical} practical questions...")
        prac_questions = self._generate_practical(
            context, knowledge_summary, difficulty_dist, self.num_practical
        )
        for q in prac_questions:
            q.question_id = q_id
            q_id += 1
        questions.extend(prac_questions)

        # Build coverage report
        coverage = self._build_coverage_report(questions, knowledge)

        assessment = AssessmentSet(
            session_id=session_id,
            questions=questions,
            coverage_report=coverage,
        )

        logger.info(
            f"Generated {len(questions)} questions: "
            f"{len(mcq_questions)} MCQ, {len(subj_questions)} subjective, "
            f"{len(prac_questions)} practical"
        )

        return assessment

    def _generate_mcqs(
        self,
        context: str,
        knowledge_summary: str,
        difficulty_dist: str,
        count: int,
    ) -> list[Question]:
        """Generate MCQ questions."""
        user_prompt = MCQ_GENERATION_USER.format(
            count=count,
            difficulty_distribution=difficulty_dist,
            context=context[:8000],
            knowledge_summary=knowledge_summary[:3000],
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": MCQ_GENERATION_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=4000,
            response_format={"type": "json_object"},
        )

        data = safe_json_parse(response.choices[0].message.content)
        if isinstance(data, dict):
            data = data.get("questions", data.get("mcqs", [data]))

        questions = []
        for item in data[:count]:
            try:
                options = [
                    MCQOption(label=opt["label"], text=opt["text"])
                    for opt in item.get("options", [])
                ]
                q = Question(
                    question_id=0,
                    question_type=QuestionType.MCQ,
                    difficulty=Difficulty(item.get("difficulty", "medium")),
                    question_text=item["question_text"],
                    context_hint=item.get("context_hint", ""),
                    source_type=SourceType(item.get("source_type", "pdf")),
                    options=options,
                    correct_option=item.get("correct_option", ""),
                    expected_answer=item.get("expected_answer", ""),
                )
                questions.append(q)
            except Exception as e:
                logger.warning(f"Failed to parse MCQ: {e}")

        return questions

    def _generate_subjective(
        self,
        context: str,
        knowledge_summary: str,
        difficulty_dist: str,
        count: int,
    ) -> list[Question]:
        """Generate subjective questions."""
        user_prompt = SUBJECTIVE_GENERATION_USER.format(
            count=count,
            difficulty_distribution=difficulty_dist,
            context=context[:8000],
            knowledge_summary=knowledge_summary[:3000],
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SUBJECTIVE_GENERATION_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=4000,
            response_format={"type": "json_object"},
        )

        data = safe_json_parse(response.choices[0].message.content)
        if isinstance(data, dict):
            data = data.get("questions", data.get("subjective", [data]))

        questions = []
        for item in data[:count]:
            try:
                q = Question(
                    question_id=0,
                    question_type=QuestionType.SUBJECTIVE,
                    difficulty=Difficulty(item.get("difficulty", "medium")),
                    question_text=item["question_text"],
                    context_hint=item.get("context_hint", ""),
                    source_type=SourceType(item.get("source_type", "pdf")),
                    expected_answer=item.get("expected_answer", ""),
                    grading_rubric=item.get("grading_rubric", ""),
                )
                questions.append(q)
            except Exception as e:
                logger.warning(f"Failed to parse subjective question: {e}")

        return questions

    def _generate_practical(
        self,
        context: str,
        knowledge_summary: str,
        difficulty_dist: str,
        count: int,
    ) -> list[Question]:
        """Generate practical assignment questions."""
        user_prompt = PRACTICAL_GENERATION_USER.format(
            count=count,
            difficulty_distribution=difficulty_dist,
            context=context[:8000],
            knowledge_summary=knowledge_summary[:3000],
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": PRACTICAL_GENERATION_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=5000,
            response_format={"type": "json_object"},
        )

        data = safe_json_parse(response.choices[0].message.content)
        if isinstance(data, dict):
            data = data.get("questions", data.get("tasks", data.get("practical", [data])))

        questions = []
        for item in data[:count]:
            try:
                q = Question(
                    question_id=0,
                    question_type=QuestionType.PRACTICAL,
                    difficulty=Difficulty(item.get("difficulty", "medium")),
                    question_text=item.get("question_text", ""),
                    context_hint=item.get("context_hint", ""),
                    source_type=SourceType(item.get("source_type", "pdf")),
                    expected_answer=item.get("expected_answer", ""),
                    grading_rubric=item.get("grading_rubric", ""),
                    task_description=item.get("task_description", ""),
                    deliverables=item.get("deliverables", []),
                )
                questions.append(q)
            except Exception as e:
                logger.warning(f"Failed to parse practical question: {e}")

        return questions

    def _extract_topics(self, knowledge: ExtractedKnowledge) -> list[str]:
        """Extract main topics from knowledge for RAG retrieval."""
        topics = []
        for unit in knowledge.units:
            if unit.topic:
                topics.append(unit.topic)
            topics.extend(unit.concepts[:3])  # Top 3 concepts per unit

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for t in topics:
            if t.lower() not in seen:
                seen.add(t.lower())
                unique.append(t)

        return unique[:15]  # Limit to 15 topics

    def _build_knowledge_summary(self, knowledge: ExtractedKnowledge) -> str:
        """Build a summary of extracted knowledge for question context."""
        parts = []
        for unit in knowledge.units:
            unit_summary = f"Topic: {unit.topic}\n"
            if unit.concepts:
                unit_summary += f"Concepts: {', '.join(unit.concepts[:5])}\n"
            if unit.steps:
                unit_summary += f"Steps: {', '.join(unit.steps[:3])}\n"
            if unit.tools:
                unit_summary += f"Tools: {', '.join(unit.tools)}\n"
            if unit.common_issues:
                unit_summary += f"Common Issues: {', '.join(unit.common_issues[:3])}\n"
            parts.append(unit_summary)

        return "\n---\n".join(parts)

    def _get_difficulty_distribution(self, mix: str) -> str:
        """Get difficulty distribution string for prompts."""
        distributions = {
            "balanced": "Mix of easy (30%), medium (40%), hard (30%)",
            "easy": "Mostly easy (60%), some medium (30%), few hard (10%)",
            "medium": "Few easy (20%), mostly medium (60%), some hard (20%)",
            "hard": "Few easy (10%), some medium (30%), mostly hard (60%)",
        }
        return distributions.get(mix, distributions["balanced"])

    def _build_coverage_report(
        self,
        questions: list[Question],
        knowledge: ExtractedKnowledge,
    ) -> dict:
        """Build a coverage report of generated questions."""
        pdf_questions = sum(1 for q in questions if q.source_type == SourceType.PDF)
        transcript_questions = sum(
            1 for q in questions if q.source_type == SourceType.TRANSCRIPT
        )

        return {
            "total_questions": len(questions),
            "mcq_count": sum(1 for q in questions if q.question_type == QuestionType.MCQ),
            "subjective_count": sum(
                1 for q in questions if q.question_type == QuestionType.SUBJECTIVE
            ),
            "practical_count": sum(
                1 for q in questions if q.question_type == QuestionType.PRACTICAL
            ),
            "from_pdf": pdf_questions,
            "from_transcript": transcript_questions,
            "difficulty_breakdown": {
                "easy": sum(1 for q in questions if q.difficulty == Difficulty.EASY),
                "medium": sum(1 for q in questions if q.difficulty == Difficulty.MEDIUM),
                "hard": sum(1 for q in questions if q.difficulty == Difficulty.HARD),
            },
            "topics_covered": list(set(
                q.context_hint for q in questions if q.context_hint
            )),
        }
