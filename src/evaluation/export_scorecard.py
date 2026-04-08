"""Markdown scorecard exporter for RAG benchmark results.

Generates a Markdown table comparing all runs on:
  cost-per-query, p95 latency, faithfulness, answer_relevancy, context_recall

Uploads the scorecard to GCS at::

    gs://{bucket}/scorecards/{run_id}.md

Can also query all historical runs from BigQuery for a full comparison table.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

def build_scorecard_markdown(results: list[dict]) -> str:
    """Build a Markdown scorecard table from a list of result dicts.

    Results are sorted by ``faithfulness`` descending.

    Args:
        results: List of dicts with keys matching the BQ schema.

    Returns:
        Markdown string.
    """
    if not results:
        return "_No benchmark results found._\n"

    sorted_results = sorted(results, key=lambda r: r.get("faithfulness", 0), reverse=True)

    header = (
        "| Run ID (short) | Experiment | Faithfulness | Relevancy "
        "| Context Recall | p95 Latency (ms) | Cost/Query ($) | Promoted |\n"
    )
    separator = (
        "|---|---|---:|---:|---:|---:|---:|:---:|\n"
    )

    rows = []
    for r in sorted_results:
        run_short = str(r.get("run_id", ""))[:8]
        promoted = "✅" if r.get("promoted") else "❌"
        rows.append(
            f"| {run_short} "
            f"| {r.get('experiment_name', '')} "
            f"| {r.get('faithfulness', 0):.3f} "
            f"| {r.get('answer_relevancy', 0):.3f} "
            f"| {r.get('context_recall', 0):.3f} "
            f"| {r.get('p95_latency_ms', 0):.0f} "
            f"| {r.get('cost_per_query_usd', 0):.6f} "
            f"| {promoted} |\n"
        )

    generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    md = (
        f"# RAG Experiment Scorecard\n\n"
        f"_Generated: {generated_at}_\n\n"
        f"{header}{separator}" + "".join(rows)
    )
    return md


# ---------------------------------------------------------------------------
# Single-run scorecard
# ---------------------------------------------------------------------------

def build_run_scorecard(result) -> str:  # result: BenchmarkResult
    """Build a detailed single-run scorecard Markdown."""
    data = {
        "run_id": result.run_id,
        "experiment_name": result.experiment_name,
        "faithfulness": result.faithfulness,
        "answer_relevancy": result.answer_relevancy,
        "context_recall": result.context_recall,
        "p95_latency_ms": result.p95_latency_ms,
        "cost_per_query_usd": result.cost_per_query_usd,
        "promoted": result.promoted,
        "started_at": result.started_at.isoformat(),
        "completed_at": result.completed_at.isoformat(),
        "num_samples": result.num_samples,
    }
    return build_scorecard_markdown([data])


# ---------------------------------------------------------------------------
# GCS upload
# ---------------------------------------------------------------------------

def upload_to_gcs(content: str, bucket: str, blob_path: str, project: str) -> str:
    """Upload string content to GCS and return the gs:// URI."""
    from google.cloud import storage

    client = storage.Client(project=project)
    bkt = client.bucket(bucket)
    blob = bkt.blob(blob_path)
    blob.upload_from_string(content, content_type="text/markdown; charset=utf-8")
    uri = f"gs://{bucket}/{blob_path}"
    logger.info("Uploaded scorecard to %s", uri)
    return uri


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def export_scorecard(
    result,  # BenchmarkResult
    gcs_bucket: str,
    project: str,
    results_store=None,  # Optional[ResultsStore]
) -> str:
    """Generate scorecard (all runs or single run) and upload to GCS.

    If ``results_store`` is supplied, queries all historical runs and
    builds a comparison table.  Otherwise falls back to a single-run card.

    Returns the GCS URI of the uploaded scorecard.
    """
    if results_store is not None:
        try:
            all_runs = results_store.list_runs(limit=100)
            # Include the current run if not already persisted
            run_ids = {r["run_id"] for r in all_runs}
            if result.run_id not in run_ids:
                current = {
                    "run_id": result.run_id,
                    "experiment_name": result.experiment_name,
                    "faithfulness": result.faithfulness,
                    "answer_relevancy": result.answer_relevancy,
                    "context_recall": result.context_recall,
                    "p95_latency_ms": result.p95_latency_ms,
                    "cost_per_query_usd": result.cost_per_query_usd,
                    "promoted": result.promoted,
                    "completed_at": result.completed_at.isoformat(),
                }
                all_runs.append(current)
            md = build_scorecard_markdown(all_runs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load historical runs (%s); using single-run scorecard", exc)
            md = build_run_scorecard(result)
    else:
        md = build_run_scorecard(result)

    blob_path = f"scorecards/{result.run_id}.md"
    return upload_to_gcs(md, bucket=gcs_bucket, blob_path=blob_path, project=project)


# ---------------------------------------------------------------------------
# CLI helper for local preview
# ---------------------------------------------------------------------------

def main() -> None:
    """Print a sample scorecard to stdout (for local testing)."""
    sample = [
        {
            "run_id": "abc12345-0000-0000-0000-000000000000",
            "experiment_name": "baseline",
            "faithfulness": 0.91,
            "answer_relevancy": 0.88,
            "context_recall": 0.85,
            "p95_latency_ms": 650,
            "cost_per_query_usd": 0.000041,
            "promoted": True,
        },
        {
            "run_id": "def67890-0000-0000-0000-000000000000",
            "experiment_name": "hybrid_search",
            "faithfulness": 0.94,
            "answer_relevancy": 0.91,
            "context_recall": 0.89,
            "p95_latency_ms": 820,
            "cost_per_query_usd": 0.000055,
            "promoted": False,
        },
    ]
    print(build_scorecard_markdown(sample))


if __name__ == "__main__":
    main()

