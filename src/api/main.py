"""FastAPI Cloud Run endpoint for RAG pipeline."""
from __future__ import annotations

import os
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.retrieval.vector_search import BigQueryVectorSearch
from src.generation.gemini_client import GeminiRAGClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="RAG Pipeline API",
    description="Enterprise document Q&A powered by BigQuery Vector Search + Gemini",
    version="1.0.0",
)

# Initialize clients from env vars
PROJECT_ID = os.environ["PROJECT_ID"]
LOCATION = os.getenv("LOCATION", "us-central1")
BQ_DATASET = os.getenv("BQ_DATASET", "rag_store")
BQ_TABLE = os.getenv("BQ_TABLE", "document_embeddings")

vector_search = BigQueryVectorSearch(
    project_id=PROJECT_ID,
    dataset=BQ_DATASET,
    table=BQ_TABLE,
)
gemini_client = GeminiRAGClient(
    project_id=PROJECT_ID,
    location=LOCATION,
)


class QueryRequest(BaseModel):
    question: str
    top_k: int = 10
    filters: Optional[dict] = None
    use_hybrid: bool = False


class QueryResponse(BaseModel):
    answer: str
    sources: list[str]
    confidence: float
    model: str
    chunks_retrieved: int


@app.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
) -> QueryResponse:
    """Query the RAG pipeline with a natural language question."""
    try:
        # Step 1: Embed the question
        from vertexai.language_models import TextEmbeddingModel
        import vertexai
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        embed_model = TextEmbeddingModel.from_pretrained("text-embedding-004")
        embeddings = embed_model.get_embeddings([request.question])
        query_embedding = embeddings[0].values

        # Step 2: Retrieve similar chunks
        if request.use_hybrid:
            keywords = request.question.split()
            chunks = vector_search.hybrid_search(
                query_embedding=query_embedding,
                keywords=keywords,
                top_k=request.top_k,
            )
        else:
            chunks = vector_search.search(
                query_embedding=query_embedding,
                top_k=request.top_k,
                filters=request.filters,
            )

        # Step 3: Generate grounded answer
        context = [
            {"content": c.content, "source_doc": c.source_doc, "similarity_score": c.similarity_score}
            for c in chunks
        ]
        rag_response = gemini_client.generate(
            question=request.question,
            context_chunks=context,
        )

        return QueryResponse(
            answer=rag_response.answer,
            sources=rag_response.sources,
            confidence=rag_response.confidence,
            model=rag_response.model,
            chunks_retrieved=len(chunks),
        )

    except Exception as e:
        logger.error("RAG query failed: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "service": "rag-pipeline"}
