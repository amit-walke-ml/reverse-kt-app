from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def clean_text(text: str) -> str:
    text = text.replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    return text.strip()


def collapse_blank_lines(text: str) -> str:
    lines = [ln.strip() for ln in text.split("\n")]
    out: list[str] = []
    prev_empty = False
    for ln in lines:
        empty = len(ln) == 0
        if empty and prev_empty:
            continue
        out.append(ln)
        prev_empty = empty
    return "\n".join(out).strip()
