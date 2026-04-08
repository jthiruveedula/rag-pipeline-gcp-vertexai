"""Vertex AI Search (Discovery Engine) client wrapper.

Provides a thin, testable wrapper around Google Cloud Discovery Engine
``SearchServiceClient`` for use in the retriever bake-off harness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    """A single result returned by Vertex AI Search."""
    document_id: str
    chunk_id: str = ""
    relevance_score: float = 0.0
    content_snippet: str = ""
    grounding_metadata: dict = field(default_factory=dict)


class VertexAISearchClient:
    """Wrapper around Vertex AI Search (Discovery Engine) search API.

    Usage (production)::

        client = VertexAISearchClient(
            project="my-project",
            location="global",
            data_store_id="my-datastore",
        )
        results = client.search("What is RAG?", top_k=5)

    Usage (CI stub)::

        client = VertexAISearchClient(project="", location="", data_store_id="")
        results = client.search_stub("What is RAG?", top_k=5)
    """

    def __init__(
        self,
        project: str,
        location: str = "global",
        data_store_id: str = "",
        serving_config: str = "default_config",
        page_size: int = 10,
    ) -> None:
        self.project = project
        self.location = location
        self.data_store_id = data_store_id
        self.serving_config = serving_config
        self.page_size = page_size
        self._client: Any = None  # Lazy-initialised in production

    # ------------------------------------------------------------------
    # Serving config resource name helper
    # ------------------------------------------------------------------

    @property
    def serving_config_name(self) -> str:
        return (
            f"projects/{self.project}/locations/{self.location}"
            f"/collections/default_collection/dataStores/{self.data_store_id}"
            f"/servingConfigs/{self.serving_config}"
        )

    # ------------------------------------------------------------------
    # Production path
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Lazy-initialise the Discovery Engine SearchServiceClient."""
        if self._client is None:
            try:
                from google.cloud import discoveryengine_v1beta as discoveryengine
                self._client = discoveryengine.SearchServiceClient()
            except ImportError as exc:
                raise ImportError(
                    "google-cloud-discoveryengine is required for production use. "
                    "Install with: pip install google-cloud-discoveryengine"
                ) from exc
        return self._client

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_expr: str = "",
    ) -> list[SearchResult]:
        """Run a search against Vertex AI Search and return results.

        Args:
            query: Natural language search query.
            top_k: Maximum number of results to return.
            filter_expr: Optional filter expression (Discovery Engine syntax).

        Returns:
            List of SearchResult objects sorted by relevance.
        """
        from google.cloud import discoveryengine_v1beta as discoveryengine

        client = self._get_client()
        request = discoveryengine.SearchRequest(
            serving_config=self.serving_config_name,
            query=query,
            page_size=top_k,
            filter=filter_expr,
            content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
                snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                    return_snippet=True,
                    max_snippet_count=1,
                ),
                extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                    max_extractive_answer_count=1,
                ),
            ),
        )
        response = client.search(request)
        results = []
        for item in response.results:
            doc = item.document
            snippet = ""
            if item.chunk_info and item.chunk_info.content:
                snippet = item.chunk_info.content
            results.append(
                SearchResult(
                    document_id=doc.id,
                    chunk_id=doc.id,
                    relevance_score=getattr(item, "relevance_score", 0.0),
                    content_snippet=snippet,
                    grounding_metadata={},
                )
            )
        return results

    # ------------------------------------------------------------------
    # Stub path (CI / testing)
    # ------------------------------------------------------------------

    def search_stub(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """Return synthetic results for CI/testing without GCP calls."""
        return [
            SearchResult(
                document_id=f"doc_{i}",
                chunk_id=f"vai_chunk_{i}",
                relevance_score=1.0 / (i + 1),
                content_snippet=f"Stub content for query: {query} (result {i})",
            )
            for i in range(top_k)
        ]

    def retrieve_chunk_ids_stub(self, query: str, top_k: int = 5) -> list[str]:
        """Convenience wrapper returning only chunk IDs."""
        return [r.chunk_id for r in self.search_stub(query, top_k)]

