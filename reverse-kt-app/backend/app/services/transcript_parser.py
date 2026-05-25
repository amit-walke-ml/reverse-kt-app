"""Transcript Parser Service — Processes Microsoft Teams transcripts.

Handles multiple input formats:
- .vtt (WebVTT from Teams recordings)
- .txt (plain text transcripts)
- .docx (Word document transcripts)

Performs:
- Speaker extraction and turn merging
- Filler word removal
- Text cleaning and normalization
- Topic segmentation (via LLM)
"""

import re
import logging
from pathlib import Path
from typing import Optional

from app.models.schemas import SpeakerTurn, TranscriptDocument
from app.utils.helpers import clean_text, remove_filler_words

logger = logging.getLogger(__name__)


class TranscriptParser:
    """Parse and structure meeting transcripts from various formats."""

    # Common Teams VTT speaker tag pattern
    SPEAKER_TAG_PATTERN = re.compile(r"<v\s+([^>]+)>(.+?)(?:</v>)?$", re.DOTALL)
    # Timestamp line pattern
    TIMESTAMP_PATTERN = re.compile(
        r"(\d{1,2}:\d{2}:\d{2}[\.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[\.,]\d{3})"
    )
    # Simple speaker pattern for plain text (e.g., "John Doe: text" or "John Doe [10:30]: text")
    PLAIN_SPEAKER_PATTERN = re.compile(
        r"^([A-Z][a-zA-Z\s.'-]+?)(?:\s*\[\d{1,2}:\d{2}(?::\d{2})?\])?\s*:\s*(.+)"
    )

    def parse(self, file_path: str | Path) -> TranscriptDocument:
        """Parse a transcript file and return structured document."""
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Transcript file not found: {file_path}")

        suffix = file_path.suffix.lower()
        logger.info(f"Parsing transcript: {file_path.name} (format: {suffix})")

        if suffix == ".vtt":
            turns = self._parse_vtt(file_path)
        elif suffix == ".docx":
            turns = self._parse_docx(file_path)
        elif suffix in (".txt", ".text"):
            turns = self._parse_plain_text(file_path)
        else:
            # Try as plain text
            logger.warning(f"Unknown format {suffix}, treating as plain text")
            turns = self._parse_plain_text(file_path)

        # Merge consecutive same-speaker turns
        turns = self._merge_speaker_turns(turns)

        # Clean all turns
        turns = self._clean_turns(turns)

        # Remove empty turns
        turns = [t for t in turns if t.text.strip()]

        # Extract unique speakers
        speakers = list(set(t.speaker for t in turns if t.speaker != "Unknown"))

        # Build full clean text
        full_text = self._build_full_text(turns)

        logger.info(
            f"Extracted {len(turns)} speaker turns from {len(speakers)} speakers"
        )

        return TranscriptDocument(
            speakers=speakers,
            segments=[],  # Segments will be populated by knowledge extractor
            full_clean_text=full_text,
            metadata={
                "source_file": file_path.name,
                "format": suffix,
                "total_turns": len(turns),
                "speakers": speakers,
            },
        )

    def _parse_vtt(self, file_path: Path) -> list[SpeakerTurn]:
        """Parse a WebVTT file from Microsoft Teams."""
        content = file_path.read_text(encoding="utf-8", errors="replace")
        turns = []

        lines = content.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # Look for timestamp line
            ts_match = self.TIMESTAMP_PATTERN.match(line)
            if ts_match:
                start_time = ts_match.group(1)
                end_time = ts_match.group(2)

                # Collect text lines until next blank line or timestamp
                i += 1
                text_lines = []
                while i < len(lines) and lines[i].strip():
                    text_lines.append(lines[i].strip())
                    i += 1

                text = " ".join(text_lines)

                # Extract speaker from <v> tag
                speaker = "Unknown"
                speaker_match = self.SPEAKER_TAG_PATTERN.search(text)
                if speaker_match:
                    speaker = speaker_match.group(1).strip()
                    text = speaker_match.group(2).strip()

                # Remove any remaining HTML-like tags
                text = re.sub(r"<[^>]+>", "", text).strip()

                if text:
                    turns.append(
                        SpeakerTurn(
                            speaker=speaker,
                            text=text,
                            start_time=start_time,
                            end_time=end_time,
                        )
                    )
            i += 1

        return turns

    def _parse_docx(self, file_path: Path) -> list[SpeakerTurn]:
        """Parse a DOCX transcript file."""
        try:
            from docx import Document
        except ImportError:
            logger.error("python-docx not installed. Install with: pip install python-docx")
            raise ImportError("python-docx is required for .docx files")

        doc = Document(str(file_path))
        full_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return self._text_to_turns(full_text)

    def _parse_plain_text(self, file_path: Path) -> list[SpeakerTurn]:
        """Parse a plain text transcript."""
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return self._text_to_turns(content)

    def _text_to_turns(self, text: str) -> list[SpeakerTurn]:
        """Convert raw text into speaker turns by detecting speaker patterns."""
        lines = text.split("\n")
        turns = []
        current_speaker = "Unknown"
        current_text = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            speaker_match = self.PLAIN_SPEAKER_PATTERN.match(line)
            if speaker_match:
                # Save previous turn
                if current_text:
                    turns.append(
                        SpeakerTurn(
                            speaker=current_speaker,
                            text=" ".join(current_text),
                        )
                    )
                current_speaker = speaker_match.group(1).strip()
                current_text = [speaker_match.group(2).strip()]
            else:
                current_text.append(line)

        # Don't forget last turn
        if current_text:
            turns.append(
                SpeakerTurn(
                    speaker=current_speaker,
                    text=" ".join(current_text),
                )
            )

        return turns

    def _merge_speaker_turns(self, turns: list[SpeakerTurn]) -> list[SpeakerTurn]:
        """Merge consecutive turns from the same speaker."""
        if not turns:
            return turns

        merged = [turns[0].model_copy()]
        for turn in turns[1:]:
            prev = merged[-1]
            if turn.speaker == prev.speaker:
                prev.text += " " + turn.text
                prev.end_time = turn.end_time
            else:
                merged.append(turn.model_copy())

        return merged

    def _clean_turns(self, turns: list[SpeakerTurn]) -> list[SpeakerTurn]:
        """Clean text in all turns: remove fillers, normalize."""
        cleaned = []
        for turn in turns:
            text = remove_filler_words(turn.text)
            text = clean_text(text)
            cleaned.append(
                SpeakerTurn(
                    speaker=turn.speaker,
                    text=text,
                    start_time=turn.start_time,
                    end_time=turn.end_time,
                )
            )
        return cleaned

    def _build_full_text(self, turns: list[SpeakerTurn]) -> str:
        """Build full clean transcript text from turns."""
        lines = []
        for turn in turns:
            timestamp = ""
            if turn.start_time:
                timestamp = f" [{turn.start_time}]"
            lines.append(f"{turn.speaker}{timestamp}: {turn.text}")
        return "\n\n".join(lines)
