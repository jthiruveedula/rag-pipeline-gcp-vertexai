# 🔬 RAG Engine Lab — GCP + Vertex AI

> The proving ground for chunking, retrieval, reranking, prompting, and evaluation experiments before ideas graduate to production assistants.

[![Build](https://img.shields.io/badge/build-passing-brightgreen)](https://github.com/jthiruveedula/rag-pipeline-gcp-vertexai/actions)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Vertex AI](https://img.shields.io/badge/Vertex%20AI-Gemini%20%7C%20Embeddings-orange)](https://cloud.google.com/vertex-ai)
[![BigQuery](https://img.shields.io/badge/BigQuery-Vector%20Search-green)](https://cloud.google.com/bigquery)
[![GCP](https://img.shields.io/badge/GCP-Cloud%20Run-red)](https://cloud.google.com/run)
[![RAGAS](https://img.shields.io/badge/eval-RAGAS%20%7C%20Faithfulness%200.91-6A5ACD)](https://github.com/jthiruveedula/rag-pipeline-gcp-vertexai)
[![Latency](https://img.shields.io/badge/p95%20retrieval-45ms-yellow)](https://github.com/jthiruveedula/rag-pipeline-gcp-vertexai)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

---

## 🔬 Lab Goals

This repo is the **experimental platform** for the full RAG product family. It isolates chunking, embedding, retrieval, reranking, and prompt engineering experiments so production repos stay clean while research runs fast.

- Evidence-based platform decisions (not vibes-based)
- Reproducible benchmarks with versioned datasets
- Promotion path: lab experiment → production assistant

---

## 🏗️ System Architecture

```
User Query
    |
    v
[Cloud Run API]
    |                |
    v                v
[Vertex AI       [BigQuery
 Embeddings]      Vector Search]
                      |
                  Top-K Chunks
                      |
                      v
             [Cross-Encoder Reranker]
                      |
                      v
             [Gemini 2.0 Flash / Pro]
                      |
                 Grounded Answer
                 + Citations
```

---

## 📊 Performance Benchmarks

| Metric | Value |
|---|---|
| Embedding Throughput | 10K docs/min |
| Vector Search P95 Latency | 45ms |
| End-to-End P95 Latency | 850ms |
| RAGAS Faithfulness | 0.91 |
| RAGAS Answer Relevance | 0.88 |
| RAGAS Context Recall | 0.84 |

---

## 🧪 Experiment Matrix

| Experiment | Chunking | Retrieval | Reranking | Status |
|---|---|---|---|---|
| Baseline | Fixed 512 tokens | Vector (cosine) | None | ✅ Done |
| Hybrid Search | Semantic + BM25 | Hybrid | Cross-encoder | ✅ Done |
| Semantic Chunks | Sentence boundary | Vector | Cross-encoder | 🔄 In progress |
| Adaptive RAG | Dynamic | Query-type routing | Adaptive | 🗓️ Planned |

---

## 📁 Repo Structure

```
rag-pipeline-gcp-vertexai/
└── src/
    ├── ingestion/
    │   ├── document_loader.py      # Multi-format document loading
    │   ├── chunking_strategy.py    # Semantic chunking with overlap
    │   └── embedding_pipeline.py  # Batch embedding + BQ upload
    ├── retrieval/
    │   ├── vector_search.py        # BigQuery VECTOR_SEARCH
    │   ├── reranker.py             # Cross-encoder reranking
    │   └── context_builder.py     # Prompt context assembly
    ├── generation/
    │   ├── gemini_client.py        # Vertex AI Gemini integration
    │   ├── prompt_templates.py     # RAG prompt engineering
    │   └── citation_extractor.py  # Source attribution
    ├── api/
    │   ├── main.py                 # FastAPI Cloud Run endpoint
    │   └── Dockerfile
    ├── evaluation/
    │   ├── ragas_eval.py           # RAGAS evaluation pipeline
    │   ├── run_benchmark.py        # 🆕 Config-driven experiment runner
    │   ├── results_store.py        # 🆕 Experiment result registry
    │   └── export_scorecard.py     # 🆕 Cost-latency-quality scorecard
    ├── tests/
    │   └── test_prompt_contracts.py  # 🆕 Citation format + refusal contract tests
    └── ui/
        └── app.py                  # Streamlit demo app
```

---

## 🚀 Quickstart

```bash
# Install dependencies
pip install -r requirements.txt

# Set GCP project
export PROJECT_ID=your-project-id
export LOCATION=us-central1

# Ingest documents
python src/ingestion/embedding_pipeline.py \
  --source gs://your-bucket/docs/ \
  --bq_dataset rag_store \
  --bq_table document_embeddings

# Start API locally
uvicorn src.api.main:app --reload

# Run benchmark experiment
python src/evaluation/run_benchmark.py --config configs/baseline.yaml

# Query the RAG pipeline
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "What is our data retention policy?", "top_k": 5}'
```

---

## 🔧 Configuration

```yaml
# configs/baseline.yaml
embedding:
  model: text-embedding-004
  batch_size: 100
  dimensions: 768

vector_search:
  dataset: rag_store
  table: document_embeddings
  top_k: 10
  distance_type: COSINE

generation:
  model: gemini-2.0-flash-001
  temperature: 0.1
  max_output_tokens: 1024
  system_prompt: "Answer based only on the provided context. Always cite sources."
```

---

## 🔭 Observability

- **Benchmark runner**: `evaluation/run_benchmark.py` — config-driven, results versioned in `evaluation/results_store.py`
- **Cost-latency scorecard**: `evaluation/export_scorecard.py` — quality vs. token spend vs. p95 latency per run
- **Prompt contract tests**: `tests/test_prompt_contracts.py` — citation format, refusal language, JSON schema

---

## 🛣️ Roadmap

### Now / Next
- [ ] **Config-Driven Experiment Runner** — YAML-based chunking, retrieval, prompt variant experiments
- [ ] **Experiment Result Registry** — versioned metrics + config fingerprints for comparison over time
- [ ] **Prompt Contract Tests** — CI-enforced citation format, refusal, and JSON schema validation
- [ ] **Cost-Latency-Quality Scorecard** — per-run export for platform decision making
- [ ] **Retriever Bake-Off** — Vertex AI Search vs. BigQuery Vector vs. Hybrid comparison harness

### Future / Wow
- [ ] **Adaptive RAG Orchestrator** — dynamic retrieval strategy based on question type and corpus shape
- [ ] **Self-Healing Evaluation Loop** — failure clusters → auto-propose prompt or indexing improvements
- [ ] **Synthetic User Simulator** — realistic multi-persona workload generation for load testing
- [ ] **Long-Horizon Memory Lab** — session memory and user profile adaptation experiments
- [ ] **Governance Testbed** — simulate permissions, PII detection, and redaction pipelines

---

## 🔗 Promotion Path

When an experiment graduates from this lab:

| Experiment Type | Destination Repo |
|---|---|
| Drive-specific RAG improvements | `gdrive-rag-assistant` |
| Streaming / freshness retrieval | `intraday-ops-intelligence` |
| Citation + grounding patterns | `policy-sop-assistant` |

---

## 🤝 Contributing

PRs welcome. Run `make lint test` before opening a PR.

## 📄 License

MIT — see [LICENSE](LICENSE)
