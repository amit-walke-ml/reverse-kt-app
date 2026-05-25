"""Structured PDF extraction: headings hierarchy, lists, tables as readable text."""

from __future__ import annotations

from dataclasses import dataclass, field


import fitz


@dataclass
class PdfBlock:
    level: str  # title | paragraph | table | list
    content: str
    page_number: int
    section_titles: list[str] = field(default_factory=list)


def parse_pdf_bytes(data: bytes) -> tuple[str, list[PdfBlock]]:
    doc = fitz.open(stream=data, filetype="pdf")
    spans_sizes: list[float] = []

    for page in doc:
        d = page.get_text("dict")
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []):
                for sp in line.get("spans", []):
                    sz = float(sp.get("size", 0))
                    if sz > 0:
                        spans_sizes.append(sz)

    body_median = sorted(spans_sizes)[len(spans_sizes) // 2] if spans_sizes else 11.0

    blocks_out: list[PdfBlock] = []
    outline: list[tuple[int, str]] = []  # (level, title_text)

    def breadcrumbs() -> list[str]:
        return [t for _, t in outline]

    for page_index, page in enumerate(doc):
        pw = float(page.rect.width)

        try:
            finder = page.find_tables()
            for tab in finder.tables:
                try:
                    cells = tab.extract()
                    lines = []
                    for row in cells or []:
                        row = [" ".join((c or "").split()) for c in row]
                        lines.append(" | ".join(row))
                    if lines:
                        table_text = "[TABLE]\n" + "\n".join(lines)
                        blocks_out.append(
                            PdfBlock("table", table_text, page_index + 1, breadcrumbs()),
                        )
                except Exception:
                    continue
        except Exception:
            pass

        d = page.get_text("dict")
        chunk_lines: list[str] = []

        def flush_para() -> None:
            nonlocal chunk_lines
            txt = "\n".join(chunk_lines).strip()
            chunk_lines = []
            if txt:
                blocks_out.append(PdfBlock("paragraph", txt, page_index + 1, breadcrumbs()))

        def _guess_heading_level(span_size: float) -> int | None:
            if span_size >= body_median * 1.35:
                return 1
            if span_size >= body_median * 1.18:
                return 2
            if span_size >= body_median * 1.08:
                return 3
            return None

        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []):
                line_parts: list[str] = []
                heading_level = 0
                for sp in line.get("spans", []):
                    t = (sp.get("text") or "").strip()
                    if not t:
                        continue
                    sz = float(sp.get("size", body_median))
                    gh = _guess_heading_level(sz)
                    if gh is not None:
                        heading_level = max(heading_level, gh)
                    line_parts.append(t)
                line_text = " ".join(line_parts).strip()
                if not line_text:
                    continue

                x0 = line.get("spans", [{}])[0].get("origin", [0, 0])[0]
                is_bullet = line_text[:1] in ("•", "-", "·") or line_text[:2] in ("* ", "- ")

                if heading_level > 0 and len(line_text) < 120 and x0 < pw * 0.15:
                    flush_para()
                    outline = [(lv, tit) for lv, tit in outline if lv < heading_level]
                    outline.append((heading_level, line_text))
                    blocks_out.append(
                        PdfBlock("title", line_text, page_index + 1, breadcrumbs()[:-1]),
                    )
                    continue

                if is_bullet:
                    flush_para()
                    blocks_out.append(
                        PdfBlock("list", line_text, page_index + 1, breadcrumbs()),
                    )
                    continue

                chunk_lines.append(line_text)
        flush_para()

    full_text = _blocks_to_markdown(blocks_out)
    return full_text, blocks_out


def _blocks_to_markdown(blocks: list[PdfBlock]) -> str:
    parts: list[str] = []
    prev_page = 0
    for b in blocks:
        if b.page_number != prev_page:
            parts.append(f"\n\n<!-- page {b.page_number} -->\n")
            prev_page = b.page_number
        prefix = ""
        if b.level == "title":
            depth = min(3, max(1, len(b.section_titles)))
            prefix = "#" * depth + " "
        elif b.level == "list":
            prefix = "- "
        parts.append(prefix + b.content)
    return "\n\n".join(p for p in parts if p.strip())
