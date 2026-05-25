"""Utility helpers for the KT Assessment System."""

import json
import re
import uuid
import tiktoken
import logging
from typing import Any

logger = logging.getLogger(__name__)


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return uuid.uuid4().hex[:12]


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """Count tokens in text using tiktoken."""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text))


def chunk_text_by_tokens(
    text: str,
    max_tokens: int = 500,
    overlap_tokens: int = 50,
    model: str = "gpt-4o"
) -> list[str]:
    """Split text into chunks of approximately max_tokens with overlap."""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")

    tokens = encoding.encode(text)
    chunks = []
    start = 0

    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunk_tokens = tokens[start:end]
        chunk_text = encoding.decode(chunk_tokens)
        chunks.append(chunk_text)

        if end >= len(tokens):
            break
        start = end - overlap_tokens

    return chunks


def clean_text(text: str) -> str:
    """Basic text cleaning: normalize whitespace, remove control chars."""
    # Remove control characters except newlines and tabs
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    # Normalize whitespace (keep single newlines)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def remove_filler_words(text: str) -> str:
    """Remove common filler words from transcript text."""
    fillers = [
        r'\b(um+|uh+|eh+|ah+|hmm+|huh|mhm|uh-huh)\b',
        r'\b(you know|i mean|like,?\s+like|sort of|kind of|basically)\b',
        r'\b(right\?)\s+',
        r'\.\.\.\s*\.\.\.',
    ]
    for pattern in fillers:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    # Clean up extra spaces
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r' ([,.])', r'\1', text)
    return text.strip()


def safe_json_parse(text: str) -> Any:
    """Parse JSON from LLM response, handling markdown code blocks."""
    # Strip markdown code fences
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (code fences)
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed, attempting repair: {e}")
        # Try to find JSON within the text
        json_match = re.search(r'[\[\{].*[\]\}]', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}...")


def truncate_text(text: str, max_chars: int = 500) -> str:
    """Truncate text to max_chars, adding ellipsis if truncated."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 3] + "..."
