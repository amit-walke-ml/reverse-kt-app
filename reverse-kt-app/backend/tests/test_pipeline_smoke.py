import io
from unittest.mock import patch

import fitz


def test_parse_pdf_structure():
    buf = io.BytesIO()
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Cache Hardening Runbook\n", fontsize=14)
    page.insert_text((72, 130), "- Step one: Verify flags\n", fontsize=11)
    doc.save(buf)
    doc.close()

    from app.services.ingestion.pdf_parser import parse_pdf_bytes

    md, blocks = parse_pdf_bytes(buf.getvalue())
    assert "Cache Hardening" in md or any("Verify flags" in b.content for b in blocks)


def test_transcript_normalize():
    from app.services.ingestion.transcript_ingest import normalize_transcript, segment_transcript_heuristic

    raw = "Um, hey team, um we decided to rollout the edge fix.\n\nYou know, thanks."
    nt = normalize_transcript(raw)
    assert "Um" not in nt
    segments = segment_transcript_heuristic(nt)
    assert len(segments) >= 1


def test_chunk_pdf_blocks():
    from app.services.ingestion.pdf_parser import PdfBlock
    from app.services.processing.chunking import chunk_pdf_blocks

    blocks = [
        PdfBlock("title", "Intro", 1, []),
        PdfBlock(
            "paragraph",
            "This paragraph explains the failover scenario with enough chars to persist.",
            1,
            ["Intro"],
        ),
    ]
    chunks = chunk_pdf_blocks(blocks)
    assert chunks and chunks[0].source == "pdf"


@patch(
    "app.services.evaluation.engine.chat_json",
    return_value={"overall_feedback": "Solid", "strong_areas": ["Recall"], "weak_areas": ["Depth"]},
)
def test_evaluation_mcq_scores(_mock_chat):
    from app.schemas.kt import McqOptionOut, QuestionPrivate, QuestionPublic
    from app.services.evaluation.engine import evaluate_answers

    pub = [
        QuestionPublic(
            question_id=1,
            question_type="mcq",
            question_text="Pick true statement",
            options=[
                McqOptionOut(label="A", text="one"),
                McqOptionOut(label="B", text="two"),
            ],
        )
    ]
    prv = [
        QuestionPrivate(question_id=1, question_type="mcq", correct_option="B", reference_answer="", rubric_points=[])
    ]

    low = evaluate_answers(None, pub, prv, {1: "A"})
    high = evaluate_answers(None, pub, prv, {1: "B"})
    assert low.evaluations[0].score < high.evaluations[0].score
