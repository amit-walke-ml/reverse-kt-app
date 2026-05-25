"""PDF Parser Service — Extracts structured content from PDF documents.

Uses PyMuPDF (fitz) for fast, structure-aware extraction including:
- Headings (detected via font size analysis)
- Paragraphs
- Tables (converted to markdown)
- Lists
- Code blocks
"""

import fitz  # PyMuPDF
import logging
import re
from pathlib import Path
from collections import Counter

from app.models.schemas import DocumentSection

logger = logging.getLogger(__name__)


class PDFParser:
    """Parse PDF documents and extract structured content."""

    def __init__(self):
        self.min_heading_size_ratio = 1.15  # Font size ratio to consider as heading

    def parse(self, file_path: str | Path) -> list[DocumentSection]:
        """Parse a PDF file and return structured sections."""
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"PDF file not found: {file_path}")

        logger.info(f"Parsing PDF: {file_path.name}")
        doc = fitz.open(str(file_path))

        try:
            # Step 1: Analyze font sizes across the document
            base_font_size = self._detect_base_font_size(doc)
            logger.info(f"Detected base font size: {base_font_size:.1f}")

            # Step 2: Extract content with structure awareness
            sections = []
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_sections = self._extract_page_content(
                    page, page_num + 1, base_font_size
                )
                sections.extend(page_sections)

            # Step 3: Extract tables separately
            for page_num in range(len(doc)):
                page = doc[page_num]
                table_sections = self._extract_tables(page, page_num + 1)
                sections.extend(table_sections)

            # Step 4: Merge and clean
            sections = self._merge_consecutive_sections(sections)
            sections = [s for s in sections if s.content.strip()]

            logger.info(
                f"Extracted {len(sections)} sections from {len(doc)} pages"
            )
            return sections

        finally:
            doc.close()

    def _detect_base_font_size(self, doc: fitz.Document) -> float:
        """Detect the most common (body) font size in the document."""
        size_counter = Counter()
        for page_num in range(min(len(doc), 10)):  # Sample first 10 pages
            page = doc[page_num]
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
            for block in blocks.get("blocks", []):
                if block.get("type") != 0:  # text blocks only
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if len(text) > 3:  # Skip very short spans
                            size = round(span.get("size", 12), 1)
                            size_counter[size] += len(text)

        if not size_counter:
            return 12.0
        return size_counter.most_common(1)[0][0]

    def _extract_page_content(
        self, page: fitz.Page, page_num: int, base_font_size: float
    ) -> list[DocumentSection]:
        """Extract structured content from a single page."""
        sections = []
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

        for block in blocks.get("blocks", []):
            if block.get("type") != 0:  # Skip image blocks
                continue

            block_text = ""
            block_max_size = 0
            is_bold = False

            for line in block.get("lines", []):
                line_text = ""
                for span in line.get("spans", []):
                    line_text += span.get("text", "")
                    span_size = span.get("size", 12)
                    if span_size > block_max_size:
                        block_max_size = span_size
                    flags = span.get("flags", 0)
                    if flags & 2 ** 4:  # Bold flag
                        is_bold = True
                block_text += line_text + "\n"

            block_text = block_text.strip()
            if not block_text:
                continue

            # Determine section type
            section_type, level = self._classify_block(
                block_text, block_max_size, base_font_size, is_bold
            )

            sections.append(
                DocumentSection(
                    section_type=section_type,
                    content=block_text,
                    level=level,
                    page_number=page_num,
                    metadata={
                        "font_size": block_max_size,
                        "is_bold": is_bold,
                    },
                )
            )

        return sections

    def _classify_block(
        self,
        text: str,
        font_size: float,
        base_font_size: float,
        is_bold: bool,
    ) -> tuple[str, int]:
        """Classify a text block as heading, paragraph, list, or code."""
        # Check for list patterns
        lines = text.strip().split("\n")
        list_pattern = re.compile(r"^[\s]*[-•●◦▪*]\s|^[\s]*\d+[.)]\s|^[\s]*[a-zA-Z][.)]\s")
        list_lines = sum(1 for line in lines if list_pattern.match(line))
        if list_lines > 0 and list_lines >= len(lines) * 0.5:
            return "list", 0

        # Check for code-like content
        code_indicators = ["{", "}", "=>", "->", "def ", "class ", "import ",
                           "function ", "const ", "var ", "let ", "$ ", ">>>"]
        if any(indicator in text for indicator in code_indicators):
            code_lines = sum(
                1 for line in lines
                if any(ind in line for ind in code_indicators)
            )
            if code_lines >= len(lines) * 0.4:
                return "code", 0

        # Check for heading based on font size
        size_ratio = font_size / base_font_size if base_font_size > 0 else 1

        if size_ratio >= 1.8:
            return "heading", 1
        elif size_ratio >= 1.5:
            return "heading", 2
        elif size_ratio >= self.min_heading_size_ratio or (
            is_bold and len(text) < 100 and "\n" not in text.strip()
        ):
            return "heading", 3

        return "paragraph", 0

    def _extract_tables(
        self, page: fitz.Page, page_num: int
    ) -> list[DocumentSection]:
        """Extract tables from a page and convert to markdown format."""
        sections = []
        try:
            tables = page.find_tables()
            for table in tables:
                data = table.extract()
                if not data or len(data) < 2:
                    continue

                # Convert to markdown table
                md_table = self._table_to_markdown(data)
                if md_table:
                    sections.append(
                        DocumentSection(
                            section_type="table",
                            content=md_table,
                            level=0,
                            page_number=page_num,
                            metadata={"rows": len(data), "cols": len(data[0])},
                        )
                    )
        except Exception as e:
            logger.warning(f"Table extraction failed on page {page_num}: {e}")

        return sections

    def _table_to_markdown(self, data: list[list]) -> str:
        """Convert table data to markdown format."""
        if not data:
            return ""

        # Clean cells
        cleaned = []
        for row in data:
            cleaned_row = []
            for cell in row:
                cell_text = str(cell) if cell is not None else ""
                cell_text = cell_text.replace("|", "\\|").replace("\n", " ")
                cleaned_row.append(cell_text.strip())
            cleaned.append(cleaned_row)

        # Build markdown
        lines = []
        # Header row
        lines.append("| " + " | ".join(cleaned[0]) + " |")
        # Separator
        lines.append("| " + " | ".join(["---"] * len(cleaned[0])) + " |")
        # Data rows
        for row in cleaned[1:]:
            # Pad row if needed
            while len(row) < len(cleaned[0]):
                row.append("")
            lines.append("| " + " | ".join(row[: len(cleaned[0])]) + " |")

        return "\n".join(lines)

    def _merge_consecutive_sections(
        self, sections: list[DocumentSection]
    ) -> list[DocumentSection]:
        """Merge consecutive paragraphs that belong together."""
        if not sections:
            return sections

        merged = [sections[0]]
        for section in sections[1:]:
            prev = merged[-1]
            # Merge consecutive paragraphs on the same page
            if (
                section.section_type == "paragraph"
                and prev.section_type == "paragraph"
                and section.page_number == prev.page_number
            ):
                prev.content += "\n" + section.content
            else:
                merged.append(section)

        return merged
