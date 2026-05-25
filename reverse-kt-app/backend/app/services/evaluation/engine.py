"""Score answers with deterministic MCQ grading + LLM scoring for written work."""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas.kt import EvaluateResponse, QuestionPrivate, QuestionPublic, SingleEvaluationOut
from app.services.ai.llm import chat_json
from app.services.ai.prompts import SYSTEM_KT_ANALYST, USER_EVALUATION
from app.services.rag.faiss_store import (
    FaissSessionIndex,
    load_index_bundle,
    retrieval_blob_from_hits,
    unique_hits_many_queries,
)


def _readiness_label(pct: float) -> str:
    if pct >= 90:
        return "Expert readiness"
    if pct >= 75:
        return "Proficient"
    if pct >= 60:
        return "Developing"
    if pct >= 40:
        return "Needs reinforcement"
    return "Insufficient readiness"


def evaluate_answers(
    index: FaissSessionIndex | None,
    questions_public: list[QuestionPublic],
    questions_private: list[QuestionPrivate],
    answers_map: dict[int, str],
) -> EvaluateResponse:
    private_by_id = {q.question_id: q for q in questions_private}
    evaluations: list[SingleEvaluationOut] = []
    mcq_total = 0.0
    subj_total = 0.0
    prac_total = 0.0
    mcq_n = subj_n = prac_n = 0

    for qp in sorted(questions_public, key=lambda x: x.question_id):
        priv = private_by_id.get(qp.question_id)
        if not priv:
            continue
        answer = (answers_map.get(qp.question_id) or "").strip()
        rag_snippets = ""
        if index is not None:
            qtext = f"{qp.question_text}\n{priv.reference_answer}"[:800]
            hits = unique_hits_many_queries(index, [qtext], cap=6)
            rag_snippets, _ = retrieval_blob_from_hits(hits)
            rag_snippets = rag_snippets[:6000]

        if qp.question_type == "mcq":
            mcq_n += 1
            letter = (answer or "").strip().upper()[:1]
            correct = (priv.correct_option or "A").strip().upper()[:1]
            is_ok = letter == correct and letter in {"A", "B", "C", "D"}
            score = 10.0 if is_ok else 0.0
            mcq_total += score
            evaluations.append(
                SingleEvaluationOut(
                    question_id=qp.question_id,
                    score=score,
                    correct_answer=f"Option {correct}",
                    explanation="Correct — selected option matches grounded KT material."
                    if is_ok
                    else "Incorrect — review the scenario details and how each option maps to the documented flow.",
                    improvement_suggestions="Revisit the referenced context and confirm decision criteria."
                    if not is_ok
                    else "Great recall — extend by articulating why the distractors fail.",
                )
            )
            continue

        eval_json = chat_json(
            [
                {"role": "system", "content": SYSTEM_KT_ANALYST},
                {
                    "role": "user",
                    "content": USER_EVALUATION.format(
                        qtype=qp.question_type,
                        question=qp.question_text,
                        reference=priv.reference_answer[:4000],
                        rag_snippets=rag_snippets or "(no index — rely on reference)",
                        answer=answer or "(empty response)",
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=900,
        )

        score = float(eval_json.get("score") or 0)
        score = max(0.0, min(10.0, score))
        if qp.question_type == "subjective":
            subj_n += 1
            subj_total += score
        else:
            prac_n += 1
            prac_total += score

        evaluations.append(
            SingleEvaluationOut(
                question_id=qp.question_id,
                score=score,
                correct_answer=str(eval_json.get("correct_answer") or priv.reference_answer)[:4000],
                explanation=str(eval_json.get("explanation") or "").strip(),
                improvement_suggestions=str(eval_json.get("improvement_suggestions") or "").strip(),
            )
        )

    total_score = sum(e.score for e in evaluations)
    max_total = 10.0 * float(len(evaluations))
    percentage = (total_score / max_total) * 100 if max_total else 0.0

    strong, weak = _strength_weakness_summary(evaluations, questions_public)

    overall = chat_json(
        [
            {"role": "system", "content": "You are a concise coach summarizing KT readiness."},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "percentage": percentage,
                        "mcq_avg": (mcq_total / mcq_n) if mcq_n else 0,
                        "subjective_avg": (subj_total / subj_n) if subj_n else 0,
                        "practical_avg": (prac_total / prac_n) if prac_n else 0,
                        "evaluations": [e.model_dump() for e in evaluations],
                    },
                    ensure_ascii=False,
                )
                + "\nReturn JSON {overall_feedback:string, strong_areas:string[], weak_areas:string[]}",
            },
        ],
        temperature=0.2,
        max_tokens=700,
    )

    return EvaluateResponse(
        total_score=total_score,
        max_total_score=max_total,
        percentage=percentage,
        mcq_score=mcq_total,
        subjective_score=subj_total,
        practical_score=prac_total,
        readiness_level=_readiness_label(percentage),
        overall_feedback=str(overall.get("overall_feedback") or "Assessment complete."),
        strong_areas=list(overall.get("strong_areas") or strong),
        weak_areas=list(overall.get("weak_areas") or weak),
        evaluations=evaluations,
    )


def _strength_weakness_summary(
    evaluations: list[SingleEvaluationOut],
    public: list[QuestionPublic],
) -> tuple[list[str], list[str]]:
    pub_map = {q.question_id: q for q in public}
    strong: list[str] = []
    weak: list[str] = []

    for ev in evaluations:
        q = pub_map.get(ev.question_id)
        qtext = q.question_text if q else ""
        snippet = qtext[:120] + ("..." if len(qtext) > 120 else "")
        if ev.score >= 8:
            strong.append(snippet or f"Question {ev.question_id}: solid understanding")
        elif ev.score < 5:
            weak.append(snippet or f"Question {ev.question_id}: revisit fundamentals")

    if not strong:
        strong = ["Keep practicing — focus on applying KT stories to procedures."]
    if not weak:
        weak = ["Stretch goals: teach-back scenarios to peers."]

    return strong[:6], weak[:6]


def load_index_optional(session_dir: Path) -> FaissSessionIndex | None:
    idx_path = session_dir / "faiss.index"
    meta_path = session_dir / "faiss_meta.json"
    if not idx_path.exists() or not meta_path.exists():
        return None
    return load_index_bundle(idx_path, meta_path)
