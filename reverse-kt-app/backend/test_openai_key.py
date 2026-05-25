#!/usr/bin/env python3
"""
Quick check that OPENAI_API_KEY in backend/.env works.

Usage (from backend/):
    python test_openai_key.py

Or anywhere:
    python path/to/backend/test_openai_key.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    backend_root = Path(__file__).resolve().parent
    env_file = backend_root / ".env"
    load_dotenv(env_file)

    try:
        from openai import OpenAI
    except ImportError:
        print("Install deps: pip install -r requirements.txt", file=sys.stderr)
        return 1

    import os

    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        print(f"No OPENAI_API_KEY in environment or {env_file}", file=sys.stderr)
        return 1

    model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small").strip()

    try:
        client = OpenAI(api_key=key)
        # Cheapest deterministic check similar to production (embeddings)
        resp = client.embeddings.create(model=model, input="ping")
        dim = len(resp.data[0].embedding)
        print(f"OK: OpenAI key works with {model} (embedding dim={dim})")
    except Exception as exc:  # noqa: BLE001 — script smoke test
        print(f"FAILED — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
