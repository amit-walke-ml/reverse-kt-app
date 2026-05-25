"""Text Processor Service — Cleaning, normalization, and semantic chunking.

Converts raw extracted content (from PDFs and transcripts) into
semantically meaningful chunks ready for embedding and RAG retrieval.
"""

import logging
import re
import hashlib
from typing import Optional

from app.models.schemas import (
    DocumentSection,
    TranscriptDocument,
    TextChunk,
    SourceType,
)
from app.utils.helpers import clean_text, count_tokens, chunk_text_by_tokens
from app.config import get_settings

logger = logging.getLogger(__name__)


class TextProcessor:
    """Process and chunk text from various sources."""

    def __init__(self):
        settings = get_settings()
        self.chunk_size = settings.CHUNK_SIZE_TOKENS
        self.chunk_overlap = settings.CHUNK_OVERLAP_TOKENS

    def process_pdf_sections(
        self,
        sections: list[DocumentSection],
        source_file: str = "",
    ) -> list[TextChunk]:
        """Convert PDF sections into semantically meaningful chunks."""
        chunks = []
        current_heading = ""
        current_content = []
        current_tokens = 0

        for section in sections:
            if section.section_type == "heading":
                # Flush current content if we have enough
                if current_content and current_tokens > 50:
                    chunks.extend(
                        self._create_chunks(
                            "\n\n".join(current_content),
                            source_type=SourceType.PDF,
                            source_file=source_file,
                            section_title=current_heading,
                            page=str(section.page_number),
                        )
                    )
                    current_content = []
                    current_tokens = 0

                current_heading = section.content.strip()
                # Add heading as prefix to next chunk
                current_content.append(f"## {current_heading}")
                current_tokens += count_tokens(current_heading)

            elif section.section_type in ("paragraph", "list", "code", "table"):
                content = section.content.strip()
                tokens = count_tokens(content)

                # If adding this would exceed chunk size, flush first
                if current_tokens + tokens > self.chunk_size * 1.5 and current_content:
                    chunks.extend(
                        self._create_chunks(
                            "\n\n".join(current_content),
                            source_type=SourceType.PDF,
                            source_file=source_file,
                            section_title=current_heading,
                            page=str(section.page_number),
                        )
                    )
                    current_content = []
                    current_tokens = 0
                    if current_heading:
                        current_content.append(f"## {current_heading}")
                        current_tokens += count_tokens(current_heading)

                current_content.append(content)
                current_tokens += tokens

        # Flush remaining content
        if current_content:
            chunks.extend(
                self._create_chunks(
                    "\n\n".join(current_content),
                    source_type=SourceType.PDF,
                    source_file=source_file,
                    section_title=current_heading,
                )
            )

        logger.info(f"Created {len(chunks)} chunks from PDF sections")
        return chunks

    def process_transcript(
        self,
        transcript: TranscriptDocument,
        source_file: str = "",
    ) -> list[TextChunk]:
        """Convert transcript into semantically meaningful chunks."""
        chunks = []

        # Use the full clean text and chunk it
        if not transcript.full_clean_text.strip():
            logger.warning("Transcript has no content after cleaning")
            return chunks

        # Split by speaker turns first for natural boundaries
        paragraphs = transcript.full_clean_text.split("\n\n")

        current_content = []
        current_tokens = 0
        current_speaker = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            tokens = count_tokens(para)

            # Extract speaker from this paragraph
            speaker_match = re.match(r"^([^:]+?)(?:\s*\[.+?\])?\s*:", para)
            speaker = speaker_match.group(1) if speaker_match else ""

            # Chunk boundary: if we exceed chunk size
            if current_tokens + tokens > self.chunk_size and current_content:
                chunks.extend(
                    self._create_chunks(
                        "\n\n".join(current_content),
                        source_type=SourceType.TRANSCRIPT,
                        source_file=source_file,
                        speaker=current_speaker,
                    )
                )
                current_content = []
                current_tokens = 0

            current_content.append(para)
            current_tokens += tokens
            if speaker:
                current_speaker = speaker

        # Flush remaining
        if current_content:
            chunks.extend(
                self._create_chunks(
                    "\n\n".join(current_content),
                    source_type=SourceType.TRANSCRIPT,
                    source_file=source_file,
                    speaker=current_speaker,
                )
            )

        logger.info(f"Created {len(chunks)} chunks from transcript")
        return chunks

    def _create_chunks(
        self,
        text: str,
        source_type: SourceType,
        source_file: str = "",
        section_title: str = "",
        page: str = "",
        speaker: str = "",
    ) -> list[TextChunk]:
        """Split text into token-bounded chunks with overlap."""
        text = clean_text(text)
        if not text.strip():
            return []

        tokens = count_tokens(text)

        # If text fits in one chunk, don't split
        if tokens <= self.chunk_size:
            chunk_id = self._generate_chunk_id(text)
            return [
                TextChunk(
                    chunk_id=chunk_id,
                    content=text,
                    source_type=source_type,
                    source_file=source_file,
                    section_title=section_title,
                    page_or_timestamp=page,
                    speaker=speaker,
                    token_count=tokens,
                )
            ]

        # Split into multiple chunks
        raw_chunks = chunk_text_by_tokens(
            text, self.chunk_size, self.chunk_overlap
        )

        chunks = []
        for i, chunk_text in enumerate(raw_chunks):
            chunk_id = self._generate_chunk_id(chunk_text)
            chunks.append(
                TextChunk(
                    chunk_id=chunk_id,
                    content=chunk_text,
                    source_type=source_type,
                    source_file=source_file,
                    section_title=section_title,
                    page_or_timestamp=page,
                    speaker=speaker,
                    token_count=count_tokens(chunk_text),
                    metadata={"chunk_index": i, "total_chunks": len(raw_chunks)},
                )
            )

        return chunks

    def _generate_chunk_id(self, text: str) -> str:
        """Generate a deterministic chunk ID from content."""
        return hashlib.md5(text.encode()).hexdigest()[:12]
