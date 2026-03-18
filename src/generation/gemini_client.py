"""Gemini LLM client for RAG answer generation."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

logger = logging.getLogger(__name__)

RAG_SYSTEM_PROMPT = """
You are an expert assistant that answers questions based ONLY on the provided context.
Rules:
1. Answer using only the information in the context below.
2. If the answer is not in the context, say "I don't have enough information to answer this."
3. Always cite the source document for each fact using [Source: <doc_name>].
4. Be concise and precise.
"""


@dataclass
class RAGResponse:
    answer: str
    sources: list[str]
    confidence: float
    model: str


class GeminiRAGClient:
    """Wraps Vertex AI Gemini for grounded RAG generation."""

    def __init__(
        self,
        project_id: str,
        location: str = "us-central1",
        model_name: str = "gemini-1.5-pro",
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> None:
        vertexai.init(project=project_id, location=location)
        self.model = GenerativeModel(
            model_name,
            system_instruction=RAG_SYSTEM_PROMPT,
        )
        self.config = GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        self.model_name = model_name

    def generate(
        self,
        question: str,
        context_chunks: list[dict],
    ) -> RAGResponse:
        """Generate grounded answer from retrieved context chunks."""
        context_text = self._build_context(context_chunks)
        prompt = f"""Context:
{context_text}

Question: {question}

Answer:"""

        logger.info("Generating answer with %s", self.model_name)
        response = self.model.generate_content(
            prompt,
            generation_config=self.config,
        )

        answer = response.text
        sources = list({chunk["source_doc"] for chunk in context_chunks})

        # Simple confidence proxy: avg similarity of top chunks
        scores = [chunk.get("similarity_score", 0.0) for chunk in context_chunks]
        confidence = sum(scores) / len(scores) if scores else 0.0

        return RAGResponse(
            answer=answer,
            sources=sources,
            confidence=round(confidence, 3),
            model=self.model_name,
        )

    def _build_context(self, chunks: list[dict]) -> str:
        parts = []
        for i, chunk in enumerate(chunks, 1):
            parts.append(
                f"[{i}] Source: {chunk['source_doc']}\n{chunk['content']}"
            )
        return "\n\n".join(parts)
