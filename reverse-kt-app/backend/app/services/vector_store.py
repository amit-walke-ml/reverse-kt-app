"""Vector Store Service — FAISS-based vector storage and retrieval.

Manages embedding generation, FAISS index creation, persistence,
and similarity-based retrieval for RAG.
"""

import json
import logging
import numpy as np
from pathlib import Path
from typing import Optional

import faiss
from openai import OpenAI

from app.config import get_settings
from app.models.schemas import TextChunk

logger = logging.getLogger(__name__)


class VectorStore:
    """FAISS-based vector store for semantic search over document chunks."""

    def __init__(self):
        settings = get_settings()
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.embedding_model = settings.OPENAI_EMBEDDING_MODEL
        self.dimensions = settings.OPENAI_EMBEDDING_DIMENSIONS
        self.max_chunks = settings.MAX_CHUNKS_PER_QUERY
        self.similarity_threshold = settings.SIMILARITY_THRESHOLD
        self.index_dir = settings.INDEX_DIR

        self.index: Optional[faiss.IndexFlatIP] = None
        self.chunks: list[TextChunk] = []

    def build_index(self, chunks: list[TextChunk], session_id: str) -> None:
        """Build FAISS index from text chunks."""
        if not chunks:
            logger.warning("No chunks to index")
            return

        logger.info(f"Building FAISS index for {len(chunks)} chunks")

        # Generate embeddings in batches
        embeddings = self._generate_embeddings(
            [c.content for c in chunks]
        )

        if embeddings is None or len(embeddings) == 0:
            logger.error("Failed to generate embeddings")
            return

        # Normalize embeddings for cosine similarity via inner product
        faiss.normalize_L2(embeddings)

        # Create index
        self.index = faiss.IndexFlatIP(self.dimensions)
        self.index.add(embeddings)
        self.chunks = chunks

        # Persist to disk
        self._save_index(session_id)

        logger.info(f"FAISS index built with {self.index.ntotal} vectors")

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        source_filter: Optional[str] = None,
    ) -> list[tuple[TextChunk, float]]:
        """Search for similar chunks given a query."""
        if self.index is None or self.index.ntotal == 0:
            logger.warning("Index is empty, cannot search")
            return []

        top_k = top_k or self.max_chunks

        # Generate query embedding
        query_embedding = self._generate_embeddings([query])
        if query_embedding is None:
            return []

        faiss.normalize_L2(query_embedding)

        # Search
        scores, indices = self.index.search(query_embedding, min(top_k * 2, self.index.ntotal))

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.chunks):
                continue
            if score < self.similarity_threshold:
                continue

            chunk = self.chunks[idx]

            # Apply source filter if specified
            if source_filter and chunk.source_type.value != source_filter:
                continue

            results.append((chunk, float(score)))

            if len(results) >= top_k:
                break

        return results

    def load_index(self, session_id: str) -> bool:
        """Load a previously saved index from disk."""
        index_path = self.index_dir / f"{session_id}.faiss"
        meta_path = self.index_dir / f"{session_id}_chunks.json"

        if not index_path.exists() or not meta_path.exists():
            logger.warning(f"No saved index found for session {session_id}")
            return False

        try:
            self.index = faiss.read_index(str(index_path))

            with open(meta_path, "r", encoding="utf-8") as f:
                chunks_data = json.load(f)
                self.chunks = [TextChunk(**c) for c in chunks_data]

            logger.info(
                f"Loaded index for session {session_id}: "
                f"{self.index.ntotal} vectors, {len(self.chunks)} chunks"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to load index: {e}")
            return False

    def _save_index(self, session_id: str) -> None:
        """Save index and chunk metadata to disk."""
        self.index_dir.mkdir(parents=True, exist_ok=True)

        index_path = self.index_dir / f"{session_id}.faiss"
        meta_path = self.index_dir / f"{session_id}_chunks.json"

        faiss.write_index(self.index, str(index_path))

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(
                [c.model_dump() for c in self.chunks],
                f,
                indent=2,
                default=str,
            )

        logger.info(f"Index saved for session {session_id}")

    def _generate_embeddings(
        self, texts: list[str], batch_size: int = 100
    ) -> Optional[np.ndarray]:
        """Generate embeddings for a list of texts using OpenAI API."""
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            # Truncate very long texts
            batch = [t[:8000] for t in batch]

            try:
                response = self.client.embeddings.create(
                    model=self.embedding_model,
                    input=batch,
                )
                batch_embeddings = [e.embedding for e in response.data]
                all_embeddings.extend(batch_embeddings)
            except Exception as e:
                logger.error(f"Embedding generation failed for batch {i}: {e}")
                return None

        return np.array(all_embeddings, dtype=np.float32)

    def get_context_for_questions(
        self,
        topics: list[str],
        top_k_per_topic: int = 3,
    ) -> str:
        """Retrieve context from multiple topics for question generation."""
        all_chunks = []
        seen_ids = set()

        for topic in topics:
            results = self.search(topic, top_k=top_k_per_topic)
            for chunk, score in results:
                if chunk.chunk_id not in seen_ids:
                    all_chunks.append((chunk, score))
                    seen_ids.add(chunk.chunk_id)

        # Sort by relevance score
        all_chunks.sort(key=lambda x: x[1], reverse=True)

        # Build context string
        context_parts = []
        for chunk, score in all_chunks[:self.max_chunks * 2]:
            source_label = f"[{chunk.source_type.value.upper()}]"
            if chunk.section_title:
                source_label += f" Section: {chunk.section_title}"
            if chunk.speaker:
                source_label += f" Speaker: {chunk.speaker}"
            context_parts.append(f"{source_label}\n{chunk.content}")

        return "\n\n---\n\n".join(context_parts)
