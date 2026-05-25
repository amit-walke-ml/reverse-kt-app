"""Knowledge Extractor Service — LLM-based structured knowledge extraction.

Uses GPT-4o to extract structured knowledge from text chunks,
producing unified knowledge representations that combine
insights from both PDF and transcript sources.
"""

import json
import logging
from typing import Optional

from openai import OpenAI

from app.config import get_settings
from app.models.schemas import (
    TextChunk,
    KnowledgeUnit,
    ExtractedKnowledge,
    SourceType,
)
from app.prompts.knowledge_extraction import (
    KNOWLEDGE_EXTRACTION_SYSTEM,
    KNOWLEDGE_EXTRACTION_USER,
    KNOWLEDGE_MERGE_SYSTEM,
    KNOWLEDGE_MERGE_USER,
    TRANSCRIPT_TOPIC_SEGMENTATION,
    TRANSCRIPT_TOPIC_SEGMENTATION_USER,
)
from app.utils.helpers import safe_json_parse, truncate_text

logger = logging.getLogger(__name__)


class KnowledgeExtractor:
    """Extract structured knowledge from text chunks using LLM."""

    def __init__(self):
        settings = get_settings()
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL

    def extract_from_chunks(
        self,
        chunks: list[TextChunk],
        session_id: str,
    ) -> ExtractedKnowledge:
        """Extract knowledge from all chunks and produce unified output."""
        logger.info(f"Extracting knowledge from {len(chunks)} chunks")

        units = []
        pdf_count = 0
        transcript_count = 0

        # Process chunks in batches (group by section/topic for efficiency)
        batch = []
        batch_tokens = 0

        for chunk in chunks:
            batch.append(chunk)
            batch_tokens += chunk.token_count

            # Process batch when it reaches ~2000 tokens
            if batch_tokens >= 2000 or chunk == chunks[-1]:
                combined_content = "\n\n---\n\n".join(c.content for c in batch)
                source_type = batch[0].source_type
                section_title = batch[0].section_title or "General"

                try:
                    unit = self._extract_single(
                        combined_content, source_type, section_title
                    )
                    if unit:
                        units.append(unit)
                        if source_type == SourceType.PDF:
                            pdf_count += 1
                        else:
                            transcript_count += 1
                except Exception as e:
                    logger.error(f"Knowledge extraction failed for batch: {e}")

                batch = []
                batch_tokens = 0

        # Merge and synthesize
        summary = self._merge_knowledge(units) if units else ""

        total_concepts = sum(len(u.concepts) for u in units)

        knowledge = ExtractedKnowledge(
            session_id=session_id,
            units=units,
            summary=summary,
            total_concepts=total_concepts,
            total_from_pdf=pdf_count,
            total_from_transcript=transcript_count,
        )

        logger.info(
            f"Extracted {len(units)} knowledge units, "
            f"{total_concepts} concepts ({pdf_count} from PDF, "
            f"{transcript_count} from transcript)"
        )

        return knowledge

    def _extract_single(
        self,
        content: str,
        source_type: SourceType,
        section_title: str,
    ) -> Optional[KnowledgeUnit]:
        """Extract knowledge from a single content batch."""
        user_prompt = KNOWLEDGE_EXTRACTION_USER.format(
            source_type=source_type.value,
            section_title=section_title,
            content=truncate_text(content, 6000),
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": KNOWLEDGE_EXTRACTION_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )

        result_text = response.choices[0].message.content
        data = safe_json_parse(result_text)

        return KnowledgeUnit(
            topic=data.get("topic", section_title),
            concepts=data.get("concepts", []),
            steps=data.get("steps", []),
            tools=data.get("tools", []),
            decisions=data.get("decisions", []),
            common_issues=data.get("common_issues", []),
            key_insights=data.get("key_insights", []),
            source_type=source_type,
            source_references=[section_title],
        )

    def _merge_knowledge(self, units: list[KnowledgeUnit]) -> str:
        """Merge multiple knowledge units into a unified summary."""
        if not units:
            return ""

        units_json = json.dumps(
            [u.model_dump() for u in units[:20]],  # Limit to 20 units
            indent=2,
            default=str,
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": KNOWLEDGE_MERGE_SYSTEM},
                    {
                        "role": "user",
                        "content": KNOWLEDGE_MERGE_USER.format(
                            knowledge_units_json=truncate_text(units_json, 10000)
                        ),
                    },
                ],
                temperature=0.3,
                max_tokens=1500,
                response_format={"type": "json_object"},
            )

            data = safe_json_parse(response.choices[0].message.content)
            return data.get("summary", "")
        except Exception as e:
            logger.error(f"Knowledge merge failed: {e}")
            # Fallback: simple concatenation of topics
            topics = list(set(u.topic for u in units if u.topic))
            return f"KT session covering: {', '.join(topics)}"

    def segment_transcript(self, transcript_text: str) -> list[dict]:
        """Segment transcript into topics using LLM."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": TRANSCRIPT_TOPIC_SEGMENTATION},
                    {
                        "role": "user",
                        "content": TRANSCRIPT_TOPIC_SEGMENTATION_USER.format(
                            transcript_text=truncate_text(transcript_text, 8000)
                        ),
                    },
                ],
                temperature=0.3,
                max_tokens=2000,
                response_format={"type": "json_object"},
            )

            data = safe_json_parse(response.choices[0].message.content)
            # The response might be wrapped in a key
            if isinstance(data, dict):
                data = data.get("segments", data.get("topics", [data]))
            return data if isinstance(data, list) else [data]
        except Exception as e:
            logger.error(f"Transcript segmentation failed: {e}")
            return []
