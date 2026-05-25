"""FAISS vector store — one index per session; metadata stored alongside."""

from __future__ import annotations

import json
from dataclasses import dataclass

import faiss  # type: ignore
import numpy as np

from app.schemas.kt import ChunkRecord
from app.services.ai.llm import embed_texts


@dataclass
class RetrievedChunk:
    score: float
    chunk: ChunkRecord


class FaissSessionIndex:
    def __init__(
        self,
        index: faiss.Index,
        chunks: list[ChunkRecord],
        embeddings: np.ndarray | None,
    ) -> None:
        self.index = index
        self.chunks = chunks

    def search(self, query: str, k: int = 16) -> list[RetrievedChunk]:
        vec = embed_texts([query])
        q = np.array(vec, dtype="float32")
        faiss.normalize_L2(q)
        scores, idxs = self.index.search(q, min(k, len(self.chunks)))
        out: list[RetrievedChunk] = []
        for score, i in zip(scores[0], idxs[0]):
            if i < 0 or i >= len(self.chunks):
                continue
            out.append(RetrievedChunk(float(score), self.chunks[i]))
        return out

    def search_mixed(self, query: str, k: int = 8) -> list[RetrievedChunk]:
        """Guarantee representation from both PDF and transcript when available."""

        hits = self.search(query, k=max(k * 4, 24))
        pdf = [h for h in hits if h.chunk.source == "pdf"]
        tr = [h for h in hits if h.chunk.source == "transcript"]
        want_pdf = max(2, k // 2)
        want_tr = max(2, k - want_pdf)
        chosen: list[RetrievedChunk] = []
        seen: set[str] = set()

        def add_list(pool: list[RetrievedChunk], limit: int) -> None:
            for h in pool:
                if len(chosen) >= k:
                    return
                if h.chunk.id in seen:
                    continue
                if limit <= 0:
                    return
                chosen.append(h)
                seen.add(h.chunk.id)
                limit -= 1

        add_list(pdf, want_pdf)
        add_list(tr, want_tr)
        for h in hits:
            if len(chosen) >= k:
                break
            if h.chunk.id in seen:
                continue
            chosen.append(h)
            seen.add(h.chunk.id)
        return chosen[:k]


def build_index_for_chunks(chunks: list[ChunkRecord]) -> tuple[faiss.Index, np.ndarray]:
    if not chunks:
        raise ValueError("No chunks to index")
    vectors = embed_texts([c.text for c in chunks])
    mat = np.array(vectors, dtype="float32")
    faiss.normalize_L2(mat)
    dim = mat.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(mat)
    return index, mat


def save_index_bundle(path_index, path_meta, chunks: list[ChunkRecord]) -> FaissSessionIndex:
    index, mat = build_index_for_chunks(chunks)
    faiss.write_index(index, str(path_index))
    meta = {"chunks": [c.model_dump() for c in chunks]}
    path_meta.parent.mkdir(parents=True, exist_ok=True)
    with open(path_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    # Index file only - reload for serving to avoid duplication
    return load_index_bundle(path_index, path_meta)


def load_index_bundle(path_index, path_meta) -> FaissSessionIndex:
    index = faiss.read_index(str(path_index))
    with open(path_meta, encoding="utf-8") as f:
        meta = json.load(f)
    chunks = [ChunkRecord.model_validate(c) for c in meta.get("chunks", [])]
    return FaissSessionIndex(index=index, chunks=chunks, embeddings=None)


def retrieval_blob_from_hits(hits: list[RetrievedChunk]) -> tuple[str, list[str]]:
    lines: list[str] = []
    ids: list[str] = []
    for h in hits:
        cid = h.chunk.id
        ids.append(cid)
        header = f"### Chunk {cid} | {h.chunk.source.upper()} | {h.chunk.section_path}"
        lines.append(header + "\n" + h.chunk.text.strip())
    return "\n\n".join(lines), ids


def distill_digest(knowledge_units: list[dict], max_chars: int = 6000) -> str:
    parts: list[str] = []
    for u in knowledge_units:
        topic = u.get("topic") or "(untitled)"
        src = u.get("source_type") or ""
        parts.append(f"- TOPIC[{src}]: {topic}")
        for key in ["concepts", "steps", "tools", "decisions", "common_issues", "key_insights"]:
            items = u.get(key) or []
            if items:
                parts.append(f"  • {key}: " + "; ".join(items[:4]))
        parts.append("")
    blob = "\n".join(parts)
    return blob[:max_chars]


def unique_hits_many_queries(index: FaissSessionIndex, queries: list[str], cap: int = 48) -> list[RetrievedChunk]:
    seen: set[str] = set()
    out: list[RetrievedChunk] = []
    for q in queries:
        for h in index.search_mixed(q, k=10):
            if h.chunk.id in seen:
                continue
            seen.add(h.chunk.id)
            out.append(h)
            if len(out) >= cap:
                return out
    return sorted(out, key=lambda x: x.score, reverse=True)[:cap]
