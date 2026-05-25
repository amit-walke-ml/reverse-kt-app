"""Generate the KT assessment (size from settings) anchored to retrieved chunks."""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import settings
from app.schemas.kt import KnowledgePayload, McqOptionOut, QuestionPrivate, QuestionPublic
from app.services.ai.llm import chat_json
from app.services.ai.prompts import SYSTEM_KT_ANALYST, USER_QUESTION_BATCH
from app.services.rag.faiss_store import (
    FaissSessionIndex,
    distill_digest,
    retrieval_blob_from_hits,
    unique_hits_many_queries,
)


def _queries_from_knowledge(payload: KnowledgePayload) -> list[str]:
    qs: list[str] = []
    for u in payload.units:
        core = [u.topic, *list(u.concepts)[:5], *list(u.tools)[:3]]
        blob = " ".join(x.strip() for x in core if x and str(x).strip())
        if blob.strip():
            qs.append(blob.strip()[:520])
    for line in (payload.summary or "").splitlines():
        line = line.strip()
        if 20 < len(line) < 400:
            qs.append(line)
    if not qs:
        qs.append("Operational knowledge recap from onboarding materials and KT session")
    return qs[:42]


_OPTION_RE = re.compile(r"^\s*([A-D])\)\s*(.+)$")


def question_bucket_split(total: int) -> tuple[int, int, int]:
    """Split total into mcq / subjective / practical counts (weights 10:8:7, sum == total)."""
    if total < 3:
        raise ValueError("question_count must be at least 3")
    weights = (10, 8, 7)
    ssum = sum(weights)
    raw = [(total * w) / ssum for w in weights]
    floors = [int(x) for x in raw]
    rem = total - sum(floors)
    fracs = sorted([(raw[i] - floors[i], i) for i in range(3)], key=lambda x: -x[0])
    for k in range(rem):
        floors[fracs[k][1]] += 1
    return floors[0], floors[1], floors[2]


def _parse_mcq_labels(options: list[str]) -> tuple[list[McqOptionOut], dict[str, str]]:
    mapped: dict[str, str] = {}
    out_opts: list[McqOptionOut] = []

    raw_opts = []
    ch = ord("A")

    # Handle both array of strings ["A)...", ..] OR array of dicts
    for opt in options:
        if isinstance(opt, dict):
            label = (opt.get("label") or "").strip()
            text = (opt.get("text") or "").strip()
            if label and text:
                raw_opts.append(f"{label}) {text}")
            continue
        raw_opts.append(str(opt))

    for opt in raw_opts:
        m = _OPTION_RE.match(opt.strip())
        if m:
            label = m.group(1).upper()
            text = m.group(2).strip()
        else:
            label = chr(ch)
            text = opt.strip()
            ch += 1
        mapped[label] = text
        out_opts.append(McqOptionOut(label=label, text=text))
    return out_opts, mapped


def _normalize_counts(data: dict[str, Any], n_mcq: int, n_sub: int, n_pra: int) -> dict[str, Any]:
    def clip(key: str, n: int) -> None:
        arr = list(data.get(key) or [])
        data[key] = arr[:n]

    clip("mcqs", n_mcq)
    clip("subjective", n_sub)
    clip("practical", n_pra)
    return data


def _topup_short_buckets(
    mcq: list[Any],
    subjective: list[Any],
    practical: list[Any],
    rag_blob: str,
    digest: str,
    difficulty_mix: str,
    diversity: str,
    *,
    targ_mcq: int,
    targ_sub: int,
    targ_pra: int,
) -> tuple[list[Any], list[Any], list[Any]]:
    """LLM occasionally returns fewer stems than targeted; extend without re-running the full batch."""
    max_rounds = 4
    for _ in range(max_rounds):
        need_m = targ_mcq - len(mcq)
        need_s = targ_sub - len(subjective)
        need_p = targ_pra - len(practical)
        if need_m <= 0 and need_s <= 0 and need_p <= 0:
            break

        requirements: list[str] = []
        if need_m > 0:
            requirements.append(f'"mcqs": array of exactly {need_m} MCQ objects')
        if need_s > 0:
            requirements.append(f'"subjective": array of exactly {need_s} subjective objects')
        if need_p > 0:
            requirements.append(f'"practical": array of exactly {need_p} practical objects')

        instruction = (
            "Return JSON ONLY with an object that may include only these keys: mcqs, subjective, practical.\n"
            "Include ONLY keys that need more items. Each array MUST have the exact length requested.\n"
            "Use the same field shapes as the main KT assessment JSON (MCQ: question_text, options as four "
            '"A) ..." strings, correct_option, rationale, context_chunk_ids_used; subjective and practical '
            "match the standard schema).\n\n"
            + "\n".join(requirements)
        )
        user_content = (
            instruction
            + "\n\nRETRIEVED CONTEXT:\n---\n"
            + rag_blob[:16000]
            + "\n---\nSTRUCTURED KNOWLEDGE OVERVIEW:\n"
            + digest
            + f"\n\nDIFFICULTY PROFILE: {difficulty_mix}"
            + diversity
        )

        blob = chat_json(
            [
                {"role": "system", "content": SYSTEM_KT_ANALYST + " Output JSON only. Obey exact array lengths."},
                {"role": "user", "content": user_content},
            ],
            temperature=0.12,
            max_tokens=8000,
        )
        if need_m > 0 and blob.get("mcqs"):
            mcq.extend(list(blob["mcqs"])[:need_m])
        if need_s > 0 and blob.get("subjective"):
            subjective.extend(list(blob["subjective"])[:need_s])
        if need_p > 0 and blob.get("practical"):
            practical.extend(list(blob["practical"])[:need_p])

        mcq = mcq[:targ_mcq]
        subjective = subjective[:targ_sub]
        practical = practical[:targ_pra]

    return mcq, subjective, practical


def _minimal_mcq_fallback(index: int) -> dict[str, Any]:
    return {
        "question_text": (
            f"Operational recall (gap-fill #{index + 1}): Which safeguard best matches prudent practice from KT "
            "before a high-impact operational change?"
        ),
        "options": [
            "A) Preconditions, approvals, rollback path, monitoring",
            "B) Deploy without validation",
            "C) Omit documentation",
            "D) Ignore on-call escalation",
        ],
        "correct_option": "A",
        "rationale": "Aligns with standard KT readiness checks.",
        "context_chunk_ids_used": [],
    }


def _minimal_subjective_fallback(index: int) -> dict[str, Any]:
    return {
        "question_text": (
            f"Gap-fill #{index + 1}: Explain a trade-off discussed in KT that influences how quickly you ship "
            "versus how safely you operate."
        ),
        "reference_answer": "Articulate a concrete compromise (risk, capacity, fidelity, tooling) tied to KT goals.",
        "rubric_points": ["Names a trade-off dimension", "States an operational implication"],
        "context_chunk_ids_used": [],
    }


def _minimal_practical_fallback(index: int) -> dict[str, Any]:
    return {
        "question_text": f"Mini-scenario #{index + 1}: Execute the documented workflow under time pressure.",
        "task_description": (
            "Outline guarded steps with a validation signal before each irreversible commitment and note when to escalate."
        ),
        "deliverables": ["Step list", "One rollback / stop condition"],
        "reference_answer": "Sequencing with checkpoints reflective of KT playbooks.",
        "rubric_points": ["Operational realism", "Explicit safeguards"],
        "context_chunk_ids_used": [],
    }


def _enforce_minimum_bucket_sizes(
    mcq: list[Any],
    subjective: list[Any],
    practical: list[Any],
    *,
    targ_mcq: int,
    targ_sub: int,
    targ_pra: int,
) -> tuple[list[Any], list[Any], list[Any]]:
    """Guarantee target bucket sizes after LLM variance (deterministic placeholders only fill gaps)."""
    mcq = list(mcq)[:targ_mcq]
    subjective = list(subjective)[:targ_sub]
    practical = list(practical)[:targ_pra]
    while len(mcq) < targ_mcq:
        mcq.append(_minimal_mcq_fallback(len(mcq)))
    while len(subjective) < targ_sub:
        subjective.append(_minimal_subjective_fallback(len(subjective)))
    while len(practical) < targ_pra:
        practical.append(_minimal_practical_fallback(len(practical)))
    return mcq, subjective, practical


def _diversity_suffix(generation_id: str = "", avoid_signatures: list[str] | None = None) -> str:
    chunks: list[str] = []
    if avoid_signatures:
        clipped = avoid_signatures[-5:]
        sig_lines = "\n".join(f"- {s}" for s in clipped)
        chunks.append(
            "\n\nUNIQUENESS / DIVERSIFICATION:\n"
            "Fingerprints from prior assessments already generated on this SAME session:\n"
            f"{sig_lines}\n"
            "Produce a DISTINCT new set — change scenarios, pitfalls, numbering, framing, ordering, emphasis.\n"
        )
    if generation_id.strip():
        chunks.append(
            f'\nGENERATION_RUN_ID="{generation_id.strip()}"\n'
            "Treat this UUID as a cryptographic-style seed affecting creative variation only.\n"
            "Do NOT include this identifier (or substring) in stems, rationales, or answer text.\n"
        )
    return "".join(chunks)


def generate_question_bank(
    idx: FaissSessionIndex,
    knowledge: KnowledgePayload,
    difficulty_mix: str = "balanced",
    *,
    question_count: int | None = None,
    generation_id: str = "",
    avoid_signatures: list[str] | None = None,
) -> tuple[list[QuestionPublic], list[QuestionPrivate]]:
    total = settings.assessment_question_count if question_count is None else question_count
    n_mcq, n_sub, n_pra = question_bucket_split(total)
    diversity = _diversity_suffix(generation_id, avoid_signatures)
    queries = _queries_from_knowledge(knowledge)
    hits = unique_hits_many_queries(idx, queries, cap=48)
    rag_blob, _ = retrieval_blob_from_hits(hits)
    digest = distill_digest([u.model_dump() for u in knowledge.units])

    max_chat_tok = min(15000, 3200 + total * 360)
    batch_fmt_kw = dict(
        difficulty_mix=difficulty_mix,
        n_mcq=n_mcq,
        n_sub=n_sub,
        n_pra=n_pra,
        rag_blob=rag_blob[:28000],
        knowledge_digest=digest,
    )
    user_prompt = USER_QUESTION_BATCH.format(**batch_fmt_kw) + diversity

    data = chat_json(
        [
            {"role": "system", "content": SYSTEM_KT_ANALYST + " Enforce numeric counts precisely."},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.35,
        max_tokens=max_chat_tok,
    )

    data = _normalize_counts(data, n_mcq, n_sub, n_pra)

    mcq = list(data.get("mcqs") or [])
    subjective = list(data.get("subjective") or [])
    practical = list(data.get("practical") or [])

    if len(mcq) != n_mcq or len(subjective) != n_sub or len(practical) != n_pra:
        repair_prompt = """The previous JSON had wrong item counts or invalid fields.
Repair it NOW. Respond JSON ONLY using the same schema as earlier instruction.
""" + (
            f"Requirements: mcqs EXACTLY {n_mcq}, subjective EXACTLY {n_sub}, practical EXACTLY {n_pra}.\n"
        ) + """Preserve factual grounding to provided sources from prior message context.

MALFORMED_PAYLOAD:
""" + json.dumps(
            {"mcqs": mcq, "subjective": subjective, "practical": practical}, ensure_ascii=False
        )

        repaired = chat_json(
            [
                {"role": "system", "content": SYSTEM_KT_ANALYST},
                {
                    "role": "user",
                    "content": USER_QUESTION_BATCH.format(**{**batch_fmt_kw, "rag_blob": rag_blob[:22000]}) + diversity,
                },
                {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
                {"role": "user", "content": repair_prompt},
            ],
            temperature=0.05,
            max_tokens=max_chat_tok,
        )
        repaired = _normalize_counts(repaired, n_mcq, n_sub, n_pra)
        mcq = repaired.get("mcqs") or mcq
        subjective = repaired.get("subjective") or subjective
        practical = repaired.get("practical") or practical

    mcq, subjective, practical = _topup_short_buckets(
        mcq,
        subjective,
        practical,
        rag_blob,
        digest,
        difficulty_mix,
        diversity,
        targ_mcq=n_mcq,
        targ_sub=n_sub,
        targ_pra=n_pra,
    )
    mcq, subjective, practical = _enforce_minimum_bucket_sizes(
        mcq,
        subjective,
        practical,
        targ_mcq=n_mcq,
        targ_sub=n_sub,
        targ_pra=n_pra,
    )
    targ_mcq, targ_sub, targ_pra = n_mcq, n_sub, n_pra

    pub: list[QuestionPublic] = []
    prv: list[QuestionPrivate] = []
    qid = 1

    def tier_difficulty(kind: str, seq: int) -> str:
        if difficulty_mix == "hard":
            return "hard"
        if difficulty_mix == "easy":
            return "easy"
        # balanced profile
        if kind == "mcq":
            return "easy" if seq < 5 else ("medium" if seq < 8 else "hard")
        return "medium" if seq % 2 == 0 else "hard"

    for i, item in enumerate(mcq[:n_mcq]):
        opts_raw = item.get("options") or []
        options, labels = _parse_mcq_labels(opts_raw)
        correct = (item.get("correct_option") or "A").strip().upper()[:1]
        if correct not in labels:
            correct = options[0].label if options else "A"

        pub.append(
            QuestionPublic(
                question_id=qid,
                question_type="mcq",
                question_text=(item.get("question_text") or "").strip(),
                difficulty=tier_difficulty("mcq", i),
                context_hint=("PDF + KT dialogue" if i % 2 == 0 else "Cross-source recall"),
                options=options,
            )
        )
        prv.append(
            QuestionPrivate(
                question_id=qid,
                question_type="mcq",
                correct_option=correct,
                reference_answer=f"Correct option: {correct}) {labels.get(correct, '')}",
                rubric_points=[item.get("rationale") or "Derived from retrieved KT context."],
                rag_chunk_ids=list(item.get("context_chunk_ids_used") or []),
            )
        )
        qid += 1

    for i, item in enumerate(subjective[:n_sub]):
        pub.append(
            QuestionPublic(
                question_id=qid,
                question_type="subjective",
                question_text=(item.get("question_text") or "").strip(),
                difficulty=tier_difficulty("subjective", i),
                context_hint="Ground your answer in how the KT session explained trade-offs.",
            )
        )
        prv.append(
            QuestionPrivate(
                question_id=qid,
                question_type="subjective",
                reference_answer=(item.get("reference_answer") or "").strip(),
                rubric_points=list(item.get("rubric_points") or []),
                rag_chunk_ids=list(item.get("context_chunk_ids_used") or []),
            )
        )
        qid += 1

    for i, item in enumerate(practical[:n_pra]):
        pub.append(
            QuestionPublic(
                question_id=qid,
                question_type="practical",
                question_text=(item.get("question_text") or "").strip(),
                difficulty=tier_difficulty("practical", i),
                context_hint="Simulate real execution / decision path from KT.",
                task_description=(item.get("task_description") or "").strip(),
                deliverables=list(item.get("deliverables") or []),
            )
        )
        prv.append(
            QuestionPrivate(
                question_id=qid,
                question_type="practical",
                reference_answer=(item.get("reference_answer") or "").strip(),
                rubric_points=list(item.get("rubric_points") or []),
                rag_chunk_ids=list(item.get("context_chunk_ids_used") or []),
            )
        )
        qid += 1

    if len(pub) != total:
        raise RuntimeError(
            f"Question generation failed integrity check ({len(pub)} != {total}) — "
            f"mcqs={len(mcq)}/{targ_mcq}, subjective={len(subjective)}/{targ_sub}, practical={len(practical)}/{targ_pra}."
        )

    return pub, prv
