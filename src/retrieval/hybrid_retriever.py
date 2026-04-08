"""Hybrid BM25 + dense vector retriever with Reciprocal Rank Fusion (RRF).

Combines sparse (BQ full-text SEARCH / BM25-equivalent) and dense
(BQ VECTOR_SEARCH cosine) results using RRF fusion with a configurable
alpha weight.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RetrievedChunk:
    chunk_id: str
    score: float = 0.0
    source: str = ""  # 'sparse' | 'dense' | 'hybrid'
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------

def reciprocal_rank_fusion(
    sparse_results: list[RetrievedChunk],
    dense_results: list[RetrievedChunk],
    k_rrf: int = 60,
    alpha: float = 0.5,
) -> list[RetrievedChunk]:
    """Fuse two ranked lists using RRF.

    Args:
        sparse_results: BM25/full-text ranked results.
        dense_results: Vector-search ranked results.
        k_rrf: RRF smoothing constant (default 60 per Cormack et al.).
        alpha: Weight for dense scores vs sparse.  0 = sparse only, 1 = dense only.

    Returns:
        Merged list sorted by fused score descending.
    """
    scores: dict[str, float] = {}

    for rank, chunk in enumerate(sparse_results, start=1):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + (
            (1 - alpha) / (k_rrf + rank)
        )

    for rank, chunk in enumerate(dense_results, start=1):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + (
            alpha / (k_rrf + rank)
        )

    # Collect all unique chunks
    chunk_map: dict[str, RetrievedChunk] = {}
    for c in sparse_results + dense_results:
        if c.chunk_id not in chunk_map:
            chunk_map[c.chunk_id] = c

    fused = [
        RetrievedChunk(
            chunk_id=cid,
            score=score,
            source="hybrid",
            metadata=chunk_map[cid].metadata,
        )
        for cid, score in scores.items()
    ]
    fused.sort(key=lambda c: c.score, reverse=True)
    return fused


# ---------------------------------------------------------------------------
# HybridRetriever
# ---------------------------------------------------------------------------

class HybridRetriever:
    """Hybrid BM25 + dense vector retriever.

    In production, ``retrieve()`` calls BigQuery SEARCH (sparse) and
    VECTOR_SEARCH (dense).  For CI/testing, ``retrieve_stub()`` returns
    deterministic synthetic results.
    """

    def __init__(
        self,
        project: str = "",
        dataset: str = "",
        table: str = "",
        embedding_column: str = "embedding",
        text_column: str = "content",
        alpha: float = 0.5,
        top_k: int = 5,
        k_rrf: int = 60,
    ) -> None:
        self.project = project
        self.dataset = dataset
        self.table = table
        self.embedding_column = embedding_column
        self.text_column = text_column
        self.alpha = alpha
        self.top_k = top_k
        self.k_rrf = k_rrf

    # ------------------------------------------------------------------
    # Production path
    # ------------------------------------------------------------------

    def _sparse_retrieve(
        self, question: str, client: Any = None
    ) -> list[RetrievedChunk]:
        """BQ full-text SEARCH (BM25-equivalent) retrieval."""
        # In production:
        # query = f"""
        #   SELECT base.chunk_id, SCORE(base) AS score
        #   FROM `{self.project}.{self.dataset}.{self.table}` AS base
        #   WHERE SEARCH(base.{self.text_column}, @query)
        #   ORDER BY score DESC LIMIT @top_k
        # """
        raise NotImplementedError("Sparse retrieval requires BigQuery client.")

    def _dense_retrieve(
        self, query_embedding: list[float], client: Any = None
    ) -> list[RetrievedChunk]:
        """BQ VECTOR_SEARCH cosine retrieval."""
        # In production:
        # query = f"""
        #   SELECT base.chunk_id, distance
        #   FROM VECTOR_SEARCH(
        #     TABLE `{self.project}.{self.dataset}.{self.table}`,
        #     '{self.embedding_column}',
        #     (SELECT @embedding),
        #     top_k => @top_k,
        #     distance_type => 'COSINE'
        #   )
        #   ORDER BY distance LIMIT @top_k
        # """
        raise NotImplementedError("Dense retrieval requires BigQuery client.")

    def retrieve(
        self,
        question: str,
        query_embedding: list[float],
        bq_client: Any = None,
    ) -> list[RetrievedChunk]:
        """Full production retrieval with RRF fusion."""
        sparse = self._sparse_retrieve(question, bq_client)
        dense = self._dense_retrieve(query_embedding, bq_client)
        fused = reciprocal_rank_fusion(
            sparse, dense, k_rrf=self.k_rrf, alpha=self.alpha
        )
        return fused[: self.top_k]

    # ------------------------------------------------------------------
    # Stub path (CI / testing)
    # ------------------------------------------------------------------

    def retrieve_stub(self, question: str) -> list[str]:
        """Return deterministic synthetic chunk IDs for CI testing."""
        sparse = [
            RetrievedChunk(chunk_id=f"sparse_chunk_{i}", score=1.0 / (i + 1), source="sparse")
            for i in range(self.top_k)
        ]
        dense = [
            RetrievedChunk(chunk_id=f"dense_chunk_{i}", score=1.0 / (i + 1), source="dense")
            for i in range(self.top_k)
        ]
        fused = reciprocal_rank_fusion(
            sparse, dense, k_rrf=self.k_rrf, alpha=self.alpha
        )
        return [c.chunk_id for c in fused[: self.top_k]]

