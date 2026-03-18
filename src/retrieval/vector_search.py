"""BigQuery Vector Search for RAG retrieval."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from google.cloud import bigquery

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    chunk_id: str
    content: str
    source_doc: str
    similarity_score: float
    metadata: dict[str, Any]


class BigQueryVectorSearch:
    """Performs semantic search using BigQuery VECTOR_SEARCH."""

    def __init__(
        self,
        project_id: str,
        dataset: str,
        table: str,
        embedding_column: str = "embedding",
        content_column: str = "content",
    ) -> None:
        self.client = bigquery.Client(project=project_id)
        self.project_id = project_id
        self.dataset = dataset
        self.table = table
        self.embedding_column = embedding_column
        self.content_column = content_column

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        distance_type: str = "COSINE",
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        """Search for similar chunks using BigQuery VECTOR_SEARCH."""
        base_table = f"`{self.project_id}.{self.dataset}.{self.table}`"
        embedding_str = str(query_embedding)

        where_clause = ""
        if filters:
            conditions = [
                f"base.{k} = '{v}'" for k, v in filters.items()
            ]
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                base.chunk_id,
                base.content,
                base.source_doc,
                base.metadata,
                distance
            FROM
                VECTOR_SEARCH(
                    TABLE {base_table},
                    '{self.embedding_column}',
                    (SELECT {embedding_str} AS query_embedding),
                    top_k => {top_k},
                    distance_type => '{distance_type}'
                )
            {where_clause}
            ORDER BY distance ASC
        """

        logger.info("Running BigQuery VECTOR_SEARCH with top_k=%d", top_k)
        results = self.client.query(query).result()

        chunks = []
        for row in results:
            chunks.append(
                RetrievedChunk(
                    chunk_id=row.chunk_id,
                    content=row.content,
                    source_doc=row.source_doc,
                    similarity_score=1.0 - row.distance,  # Convert distance to similarity
                    metadata=row.metadata or {},
                )
            )

        logger.info("Retrieved %d chunks", len(chunks))
        return chunks

    def hybrid_search(
        self,
        query_embedding: list[float],
        keywords: list[str],
        top_k: int = 10,
        vector_weight: float = 0.7,
    ) -> list[RetrievedChunk]:
        """Combine vector search with BM25 keyword scoring."""
        vector_results = self.search(query_embedding, top_k=top_k * 2)

        # Re-rank with keyword boost
        for chunk in vector_results:
            keyword_score = sum(
                1 for kw in keywords if kw.lower() in chunk.content.lower()
            ) / max(len(keywords), 1)
            chunk.similarity_score = (
                vector_weight * chunk.similarity_score
                + (1 - vector_weight) * keyword_score
            )

        return sorted(vector_results, key=lambda x: x.similarity_score, reverse=True)[:top_k]
