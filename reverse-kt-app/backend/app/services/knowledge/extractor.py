"""LLM-assisted structured knowledge extraction (mandatory before question generation)."""

from __future__ import annotations

import json

from app.schemas.kt import KnowledgePayload, KnowledgeUnitOut
from app.services.ai.llm import chat_json
from app.services.ai.prompts import (
    SYSTEM_KT_ANALYST,
    USER_KNOWLEDGE_MAP_REDUCE,
    USER_TRANSCRIPT_STRUCTURING,
)


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 80] + "\n\n...[truncated for model context]\n"


def extract_structured_knowledge(pdf_markdown: str, transcript_normalized: str) -> KnowledgePayload:
    """Structured knowledge pass from PDF markdown and/or normalized transcript."""

    pdf_md = pdf_markdown.strip()
    transcript = transcript_normalized.strip()
    if not pdf_md and not transcript:
        raise ValueError("No content available from uploaded materials.")

    structuring: dict = {}
    if transcript:
        struct_input = transcript
        if len(struct_input) > 28000:
            struct_input = _trim(struct_input, 28000)
        structuring = chat_json(
            [
                {"role": "system", "content": SYSTEM_KT_ANALYST},
                {"role": "user", "content": USER_TRANSCRIPT_STRUCTURING.format(text=struct_input)},
            ]
        )

    bundle_parts: list[str] = []
    if pdf_md and not transcript:
        bundle_parts.append(
            "NOTE: Only PDF documents were provided. Produce knowledge units from the PDF only "
            "(use source_type `pdf` for all units)."
        )
    elif transcript and not pdf_md:
        bundle_parts.append(
            "NOTE: Only meeting transcript was provided. Produce knowledge units from the transcript only "
            "(use source_type `transcript` for all units)."
        )

    if structuring:
        bundle_parts.append(
            "### Structured transcript signals (machine JSON)\n"
            + json.dumps(structuring, ensure_ascii=False)
        )
    if pdf_md:
        bundle_parts.append(
            "### PDF excerpt (normalized markdown hierarchy)\n" + _trim(pdf_md, 52000)
        )
    if transcript:
        bundle_parts.append(
            "### Transcript excerpt (normalized prose)\n" + _trim(transcript, 42000)
        )

    bundle = "\n\n".join(bundle_parts)

    extracted = chat_json(
        [
            {"role": "system", "content": SYSTEM_KT_ANALYST},
            {"role": "user", "content": USER_KNOWLEDGE_MAP_REDUCE.format(bundle=bundle)},
        ],
        temperature=0.15,
        max_tokens=8192,
    )

    summary = (extracted.get("summary") or "").strip() or "Knowledge synthesis completed."
    units_raw = extracted.get("units") or []
    units: list[KnowledgeUnitOut] = []
    for u in units_raw:
        st = (u.get("source_type") or "pdf").lower()
        if st not in ("pdf", "transcript"):
            st = "pdf"
        units.append(
            KnowledgeUnitOut(
                topic=(u.get("topic") or "General").strip(),
                source_type=st,  # type: ignore[arg-type]
                concepts=list(u.get("concepts") or []),
                steps=list(u.get("steps") or []),
                tools=list(u.get("tools") or []),
                decisions=list(u.get("decisions") or []),
                common_issues=list(u.get("common_issues") or []),
                key_insights=list(u.get("key_insights") or []),
            )
        )

    if len(units) < 6:
        has_tr = any(u.source_type == "transcript" for u in units)
        has_pdf = any(u.source_type == "pdf" for u in units)
        if transcript and not has_tr:
            units.append(
                KnowledgeUnitOut(
                    topic="Session overview",
                    source_type="transcript",
                    concepts=["Session themes recovered from KT dialogue"],
                    steps=[],
                    tools=[],
                    decisions=structuring.get("decisions", [])[:5],
                    common_issues=structuring.get("troubleshooting", [])[:5],
                    key_insights=structuring.get("key_exchanges", [])[:5],
                )
            )
        if pdf_md and not has_pdf:
            units.append(
                KnowledgeUnitOut(
                    topic="Document playbook",
                    source_type="pdf",
                    concepts=["Canonical process guidance from authored PDF"],
                    steps=[],
                    tools=[],
                    decisions=[],
                    common_issues=[],
                    key_insights=[],
                )
            )

    return KnowledgePayload(summary=summary, units=units)
