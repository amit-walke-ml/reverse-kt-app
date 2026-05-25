SYSTEM_KT_ANALYST = """You are a senior knowledge-transfer analyst and instructional designer.

You extract FACTUAL operational knowledge ONLY from supplied source text.
You MUST NOT invent tools, workflows, URLs, incidents, metrics, ownership, or timelines that are absent from sources.
Prefer concrete nouns quoted or paraphrased closely from the source.
If transcripts are noisy, salvage meaning but still DO NOT hallucinate specifics.

Respond strictly as JSON."""

USER_TRANSCRIPT_STRUCTURING = """You will receive noisy Microsoft Teams meeting transcript prose (already stripped of fillers).

Goals:
1) Identify discussion topics and important explanations.
2) Pull decisions explicitly stated ("we decided...", "aligned on...", etc.).
3) Capture Q&A or troubleshooting anecdotes if present.

Return compact JSON matching this schema:
{{
  "topics": ["string"],
  "key_exchanges": ["string"],
  "decisions": ["string"],
  "action_items": ["string"],
  "troubleshooting": ["string"],
  "notable_definitions": ["string"]
}}

TRANSCRIPT INPUT:
{text}
"""


USER_MERGE_STRUCTURED_TRANSCRIPT = """Merge the heuristic transcript segmentation with structuring signals.

Structured signals (JSON):
{signals}

Transcript excerpts (truncate-safe):
{excerpt}

Return FINAL JSON ONLY with schema:
{{"topics": [...], "key_exchanges": [...], "decisions": [...], "action_items": [...], "troubleshooting": [...], "notable_definitions": [...]}}
"""


USER_KNOWLEDGE_MAP_REDUCE = """You distill knowledge units aligned to BOTH sources: PDF onboarding documents AND KT meeting transcript.

Produce units that unify concepts, workflows, tools, dependency decisions, and common pitfalls when supported by text.

Return JSON ONLY:
{{
  "summary": "2–4 paragraphs executive summary tying PDF process to what was verbally emphasized",
  "units": [
    {{
      "topic": "specific topic title",
      "source_type": "pdf" | "transcript",
      "concepts": ["..."],
      "steps": ["..."],
      "tools": ["..."],
      "decisions": ["..."],
      "common_issues": ["..."],
      "key_insights": ["..."]
    }}
  ]
}}

RULES:
- Provide at least 3 units sourced primarily from transcript (`source_type` = transcript) AND at least 3 from PDF (`source_type` = pdf). If impossible, bias toward fidelity over counts.
- `key_insights` must reflect nuanced discussion cues (risk, misunderstandings clarified, war stories).
- Strings must cite or closely paraphrase the sources; omit unknown sections as empty arrays.

SOURCES BUNDLE:
{bundle}
"""


USER_QUESTION_BATCH = """You author assessment items for Reverse KT (validate that the trainee internalized BOTH written runbooks/meetings.)

You MUST anchor every question in RETRIEVED CONTEXT below. Prefer scenario prompts involving real tooling/decisions surfaced in snippets.
Avoid generic textbook questions — include plausible distractors (MCQs) grounded in misconceptions one could derive from sloppy reading.

DIFFICULTY PROFILE: {difficulty_mix}

TASK:
Return JSON ONLY matching:
{{
  "mcqs": [
    {{
      "question_text": "…",
      "options": ["A) …", "B) …", "C) …", "D) …"],
      "correct_option": "A"|"B"|"C"|"D",
      "rationale": "…",
      "context_chunk_ids_used": ["..."]
    }}
  ],
  "subjective": [
    {{
      "question_text": "Why/how style tied to verbal discussion nuances",
      "reference_answer": "detailed keyed answer referencing transcript/PDF nuances",
      "rubric_points": ["point"],
      "context_chunk_ids_used": ["..."]
    }}
  ],
  "practical": [
    {{
      "question_text": "One–two sentence briefing of situation",
      "task_description": "multi-step realistic task mirroring KT workflow",
      "deliverables": ["bullet checklist of what trainee must outline"],
      "reference_answer": "model approach",
      "rubric_points": ["points"],
      "context_chunk_ids_used": ["..."]
    }}
  ]
}}

COUNTS REQUIRED IN THIS RESPONSE: mcqs EXACTLY {n_mcq}; subjective EXACTLY {n_sub}; practical EXACTLY {n_pra}.

RETRIEVED CONTEXT (chunks may include PDF headings or TRANSCRIPT snippets):
---
{rag_blob}
---

STRUCTURED KNOWLEDGE OVERVIEW:
{knowledge_digest}
"""


USER_EVALUATION = """Evaluate a trainee answer using BOTH the keyed reference and RAG excerpts.

Rubrics:
- MCQ: credit only if learner choice matches keyed letter; still explain misconceptions.
- Subjective / Practical: Score 0–10 for fidelity to KT goals, completeness, operational realism — not keyword bingo.

OUTPUT JSON ONLY:
{{
  "score": float,
  "correct_answer": "string (include letter for mcq)",
  "explanation": "string",
  "improvement_suggestions": "string"
}}

QUESTION_TYPE: {qtype}

QUESTION: {question}

REFERENCE/FACIT: {reference}

CONTEXT SNIPPETS:
{rag_snippets}

LEARNER_ANSWER: {answer}

"""
