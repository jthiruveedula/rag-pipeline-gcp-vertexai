"""BigQuery-backed versioned result registry for RAG experiments.

Schema (BQ table `rag_eval.experiment_results`)::

    run_id              STRING NOT NULL
    experiment_name     STRING NOT NULL
    config_fingerprint  STRING NOT NULL
    config_yaml         STRING
    faithfulness        FLOAT64
    answer_relevancy    FLOAT64
    context_recall      FLOAT64
    p95_latency_ms      FLOAT64
    cost_per_query_usd  FLOAT64
    num_samples         INT64
    promoted            BOOL
    started_at          TIMESTAMP
    completed_at        TIMESTAMP
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Optional

logger = logging.getLogger(__name__)

# BigQuery schema definition
BQ_SCHEMA = [
    {"name": "run_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "experiment_name", "type": "STRING", "mode": "REQUIRED"},
    {"name": "config_fingerprint", "type": "STRING", "mode": "REQUIRED"},
    {"name": "config_yaml", "type": "STRING", "mode": "NULLABLE"},
    {"name": "faithfulness", "type": "FLOAT64", "mode": "NULLABLE"},
    {"name": "answer_relevancy", "type": "FLOAT64", "mode": "NULLABLE"},
    {"name": "context_recall", "type": "FLOAT64", "mode": "NULLABLE"},
    {"name": "p95_latency_ms", "type": "FLOAT64", "mode": "NULLABLE"},
    {"name": "cost_per_query_usd", "type": "FLOAT64", "mode": "NULLABLE"},
    {"name": "num_samples", "type": "INT64", "mode": "NULLABLE"},
    {"name": "promoted", "type": "BOOL", "mode": "NULLABLE"},
    {"name": "started_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
    {"name": "completed_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
]

_CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.{table}` (
  run_id              STRING NOT NULL,
  experiment_name     STRING NOT NULL,
  config_fingerprint  STRING NOT NULL,
  config_yaml         STRING,
  faithfulness        FLOAT64,
  answer_relevancy    FLOAT64,
  context_recall      FLOAT64,
  p95_latency_ms      FLOAT64,
  cost_per_query_usd  FLOAT64,
  num_samples         INT64,
  promoted            BOOL,
  started_at          TIMESTAMP,
  completed_at        TIMESTAMP
)
OPTIONS (require_partition_filter = false);
"""


class ResultsStore:
    """Persist and query benchmark results in BigQuery."""

    def __init__(self, project: str, dataset: str = "rag_eval", table: str = "experiment_results"):
        self.project = project
        self.dataset = dataset
        self.table = table
        self._client = None

    def _bq(self):
        """Lazy BigQuery client."""
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client(project=self.project)
        return self._client

    @property
    def table_ref(self) -> str:
        return f"{self.project}.{self.dataset}.{self.table}"

    def ensure_table(self) -> None:
        """Create the BQ table if it does not exist."""
        ddl = _CREATE_TABLE_DDL.format(
            project=self.project, dataset=self.dataset, table=self.table
        )
        self._bq().query(ddl).result()
        logger.info("Ensured BQ table %s", self.table_ref)

    def save(self, result) -> None:  # result: BenchmarkResult
        """Insert one benchmark result row."""
        self.ensure_table()
        row = {
            "run_id": result.run_id,
            "experiment_name": result.experiment_name,
            "config_fingerprint": result.config_fingerprint,
            "config_yaml": result.config_yaml,
            "faithfulness": result.faithfulness,
            "answer_relevancy": result.answer_relevancy,
            "context_recall": result.context_recall,
            "p95_latency_ms": result.p95_latency_ms,
            "cost_per_query_usd": result.cost_per_query_usd,
            "num_samples": result.num_samples,
            "promoted": result.promoted,
            "started_at": result.started_at.isoformat(),
            "completed_at": result.completed_at.isoformat(),
        }
        errors = self._bq().insert_rows_json(self.table_ref, [row])
        if errors:
            raise RuntimeError(f"BigQuery insert errors: {errors}")
        logger.info("Saved run %s to %s", result.run_id, self.table_ref)

    def get_best_run(self, metric: str = "faithfulness") -> Optional[dict]:
        """Return the run with the highest value for the given metric."""
        query = f"""
            SELECT *
            FROM `{self.table_ref}`
            ORDER BY {metric} DESC
            LIMIT 1
        """
        rows = list(self._bq().query(query).result())
        return dict(rows[0]) if rows else None

    def get_baseline_run(self) -> Optional[dict]:
        """Return the most recent promoted (baseline) run."""
        query = f"""
            SELECT *
            FROM `{self.table_ref}`
            WHERE promoted = TRUE
            ORDER BY completed_at DESC
            LIMIT 1
        """
        rows = list(self._bq().query(query).result())
        return dict(rows[0]) if rows else None

    def list_runs(self, limit: int = 50) -> list[dict]:
        """Return recent runs ordered by completion time descending."""
        query = f"""
            SELECT
                run_id, experiment_name, config_fingerprint,
                faithfulness, answer_relevancy, context_recall,
                p95_latency_ms, cost_per_query_usd, promoted, completed_at
            FROM `{self.table_ref}`
            ORDER BY completed_at DESC
            LIMIT {limit}
        """
        return [dict(row) for row in self._bq().query(query).result()]

    def detect_regression(
        self,
        current_run,
        faithfulness_threshold: float = 0.85,
        p95_threshold_ms: float = 1200,
    ) -> dict:
        """Compare current_run against stored baseline; return regression report."""
        baseline = self.get_baseline_run()
        report = {
            "run_id": current_run.run_id,
            "faithfulness_pass": current_run.faithfulness >= faithfulness_threshold,
            "latency_pass": current_run.p95_latency_ms <= p95_threshold_ms,
            "baseline_run_id": baseline["run_id"] if baseline else None,
            "faithfulness_delta": (
                current_run.faithfulness - baseline["faithfulness"]
                if baseline
                else None
            ),
        }
        report["overall_pass"] = report["faithfulness_pass"] and report["latency_pass"]
        return report

