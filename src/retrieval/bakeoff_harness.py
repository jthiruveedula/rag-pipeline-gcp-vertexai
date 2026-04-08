"""Retriever Bake-Off harness.

Runs retrieval against three backends
  1. BigQuery VECTOR_SEARCH (cosine)
  2. Vertex AI Search native grounding
  3. Hybrid BM25 + vector with Reciprocal Rank Fusion (RRF)

for a shared eval dataset and reports context recall@k, MRR, NDCG,
p95 latency, and estimated cost-per-1000-queries.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BACKENDS = ["bq_vector", "vertex_ai_search", "hybrid_rrf"]

# Estimated cost per 1 000 queries (USD) – placeholder defaults
COST_PER_1K = {
    "bq_vector": 0.04,
    "vertex_ai_search": 1.50,
    "hybrid_rrf": 0.08,
}


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def context_recall_at_k(retrieved_ids: list[str], ground_truth_ids: list[str], k: int) -> float:
    """Fraction of ground-truth chunks found in top-k retrieved results."""
    if not ground_truth_ids:
        return 0.0
    top_k = set(retrieved_ids[:k])
    hits = sum(1 for g in ground_truth_ids if g in top_k)
    return hits / len(ground_truth_ids)


def reciprocal_rank(retrieved_ids: list[str], ground_truth_ids: list[str]) -> float:
    """Reciprocal rank of the first relevant document."""
    gt_set = set(ground_truth_ids)
    for rank, rid in enumerate(retrieved_ids, start=1):
        if rid in gt_set:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved_ids: list[str], ground_truth_ids: list[str], k: int) -> float:
    """Normalised Discounted Cumulative Gain at k."""
    import math
    gt_set = set(ground_truth_ids)
    dcg = sum(
        (1.0 / math.log2(rank + 1))
        for rank, rid in enumerate(retrieved_ids[:k], start=1)
        if rid in gt_set
    )
    ideal = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, min(len(ground_truth_ids), k) + 1)
    )
    return dcg / ideal if ideal > 0 else 0.0


def p95_latency(latencies_ms: list[float]) -> float:
    """95th-percentile latency in milliseconds."""
    if not latencies_ms:
        return 0.0
    sorted_lat = sorted(latencies_ms)
    idx = max(0, int(len(sorted_lat) * 0.95) - 1)
    return sorted_lat[idx]


# ---------------------------------------------------------------------------
# Backend stubs (replaced by real clients in production)
# ---------------------------------------------------------------------------

def _retrieve_bq_vector(question: str, k: int, config: dict) -> tuple[list[str], float]:
    """BQ VECTOR_SEARCH retrieval stub."""
    start = time.perf_counter()
    # In production: call BigQuery VECTOR_SEARCH via google-cloud-bigquery
    retrieved = [f"bq_chunk_{i}" for i in range(k)]
    elapsed_ms = (time.perf_counter() - start) * 1000
    return retrieved, elapsed_ms


def _retrieve_vertex_ai_search(question: str, k: int, config: dict) -> tuple[list[str], float]:
    """Vertex AI Search native retrieval stub."""
    start = time.perf_counter()
    # In production: call DiscoveryEngineServiceClient.search()
    retrieved = [f"vai_chunk_{i}" for i in range(k)]
    elapsed_ms = (time.perf_counter() - start) * 1000
    return retrieved, elapsed_ms


def _retrieve_hybrid_rrf(question: str, k: int, config: dict) -> tuple[list[str], float]:
    """Hybrid BM25 + vector with RRF retrieval stub."""
    from src.retrieval.hybrid_retriever import HybridRetriever
    start = time.perf_counter()
    retriever = HybridRetriever(
        alpha=config.get("retrieval", {}).get("hybrid_alpha", 0.5),
        top_k=k,
    )
    retrieved = retriever.retrieve_stub(question)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return retrieved, elapsed_ms


_BACKEND_FN = {
    "bq_vector": _retrieve_bq_vector,
    "vertex_ai_search": _retrieve_vertex_ai_search,
    "hybrid_rrf": _retrieve_hybrid_rrf,
}


# ---------------------------------------------------------------------------
# Core harness
# ---------------------------------------------------------------------------

def load_eval_dataset(path: str) -> list[dict]:
    """Load JSONL eval dataset from local path or GCS URI."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Eval dataset not found: {path}")
    records = []
    with p.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def run_backend(
    backend: str,
    dataset: list[dict],
    k: int,
    config: dict,
) -> dict[str, Any]:
    """Run a single backend against the full dataset and return metrics."""
    retrieve_fn = _BACKEND_FN[backend]
    recalls, mrrs, ndcgs, latencies = [], [], [], []

    for record in dataset:
        question = record["question"]
        ground_truth = record.get("ground_truth_chunk_ids", [])
        retrieved, lat_ms = retrieve_fn(question, k, config)
        latencies.append(lat_ms)
        recalls.append(context_recall_at_k(retrieved, ground_truth, k))
        mrrs.append(reciprocal_rank(retrieved, ground_truth))
        ndcgs.append(ndcg_at_k(retrieved, ground_truth, k))

    n = len(dataset)
    mean_recall = statistics.mean(recalls) if recalls else 0.0
    ci_95 = 1.96 * (statistics.stdev(recalls) / (n ** 0.5)) if n > 1 else 0.0

    return {
        "backend": backend,
        "n_queries": n,
        "recall_at_k": round(mean_recall, 4),
        "recall_ci_95": round(ci_95, 4),
        "mrr": round(statistics.mean(mrrs) if mrrs else 0.0, 4),
        "ndcg_at_k": round(statistics.mean(ndcgs) if ndcgs else 0.0, 4),
        "p95_latency_ms": round(p95_latency(latencies), 2),
        "est_cost_per_1k_usd": COST_PER_1K.get(backend, 0.0),
    }


def generate_comparison_table(results: list[dict]) -> str:
    """Render results as a Markdown comparison table."""
    header = "| Backend | Recall@k | MRR | NDCG@k | p95 Latency (ms) | Cost/1K ($) |"
    sep = "|---------|----------|-----|--------|-----------------|-------------|"
    rows = [
        f"| {r['backend']} | {r['recall_at_k']:.4f} ±{r['recall_ci_95']:.4f} "
        f"| {r['mrr']:.4f} | {r['ndcg_at_k']:.4f} | {r['p95_latency_ms']:.1f} "
        f"| {r['est_cost_per_1k_usd']:.2f} |"
        for r in results
    ]
    return "\n".join([header, sep] + rows)


def recommend_backend(results: list[dict]) -> str:
    """Simple heuristic: highest recall, latency <= 500 ms, lowest cost wins."""
    eligible = [r for r in results if r["p95_latency_ms"] <= 500]
    if not eligible:
        eligible = results
    best = max(eligible, key=lambda r: (r["recall_at_k"], -r["est_cost_per_1k_usd"]))
    return best["backend"]


def run_bakeoff(
    config_path: str,
    eval_dataset_path: str,
    k: int = 5,
    backends: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Entry-point for the bake-off harness."""
    with open(config_path) as fh:
        config = yaml.safe_load(fh)

    backends = backends or BACKENDS

    if dry_run:
        # Use a synthetic dataset for CI dry-runs
        dataset = [
            {"question": f"q{i}", "ground_truth_chunk_ids": [f"chunk_{i}"]}
            for i in range(5)
        ]
    else:
        dataset = load_eval_dataset(eval_dataset_path)

    results = [run_backend(b, dataset, k, config) for b in backends]
    table = generate_comparison_table(results)
    recommendation = recommend_backend(results)

    return {
        "config": config_path,
        "k": k,
        "backends_tested": backends,
        "results": results,
        "comparison_table": table,
        "recommended_backend": recommendation,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retriever Bake-Off Harness")
    parser.add_argument("--config", required=True, help="Experiment config YAML")
    parser.add_argument("--eval-dataset", default="eval_dataset.jsonl",
                        help="Path to eval dataset JSONL")
    parser.add_argument("--k", type=int, default=5, help="Recall@k")
    parser.add_argument("--backends", nargs="+", choices=BACKENDS, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    output = run_bakeoff(
        config_path=args.config,
        eval_dataset_path=args.eval_dataset,
        k=args.k,
        backends=args.backends,
        dry_run=args.dry_run,
    )
    print(output["comparison_table"])
    print(f"\nRecommended backend: {output['recommended_backend']}")

