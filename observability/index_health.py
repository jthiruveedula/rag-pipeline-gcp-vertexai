"""observability/index_health.py

Corpus health report for rag-pipeline-gcp-vertexai.
Checks BigQuery Vector Search index freshness, embedding job failure rate,
and DLQ backlog to produce a CorpusHealthReport.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

from google.cloud import bigquery, pubsub_v1

logger = logging.getLogger(__name__)

PROJECT_ID: str = os.environ["GCP_PROJECT_ID"]
CHUNKS_TABLE: str = os.environ.get(
    "CHUNKS_BQ_TABLE", f"{PROJECT_ID}.rag_pipeline.chunks"
)
DLQ_SUBSCRIPTION: str = os.environ.get(
    "DLQ_SUBSCRIPTION", f"projects/{PROJECT_ID}/subscriptions/rag-pipeline-dlq-sub"
)
STALE_THRESHOLD_MINUTES: float = float(os.environ.get("STALE_THRESHOLD_MINUTES", "60"))
FAILURE_RATE_THRESHOLD: float = float(os.environ.get("FAILURE_RATE_THRESHOLD", "0.05"))
DLQ_THRESHOLD: int = int(os.environ.get("DLQ_THRESHOLD", "10"))


@dataclass
class CorpusHealthReport:
    generated_at: str
    total_chunks: int
    total_documents: int
    chunks_by_mime: dict
    last_ingestion_ts: Optional[str]
    sync_lag_minutes: Optional[float]
    embedding_failure_rate: float
    dlq_backlog_count: int
    avg_source_age_hours: float
    status: str  # "healthy" | "degraded" | "critical"


def get_corpus_stats(client: bigquery.Client) -> dict:
    """Fetch chunk and document counts from BigQuery."""
    query = f"""
        SELECT
            COUNT(*) AS total_chunks,
            COUNT(DISTINCT source_uri) AS total_documents,
            ARRAY_AGG(
                STRUCT(mime_type, COUNT(*) AS cnt)
                ORDER BY cnt DESC LIMIT 20
            ) AS by_mime,
            AVG(
                TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), ingested_at, HOUR)
            ) AS avg_age_hours
        FROM `{CHUNKS_TABLE}`
        WHERE DATE(ingested_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
    """
    row = list(client.query(query).result())[0]
    by_mime = {r["mime_type"]: r["cnt"] for r in (row["by_mime"] or [])}
    return {
        "total_chunks": row["total_chunks"],
        "total_documents": row["total_documents"],
        "by_mime": by_mime,
        "avg_age_hours": float(row["avg_age_hours"] or 0.0),
    }


def get_last_ingestion(client: bigquery.Client) -> Optional[datetime]:
    """Return the timestamp of the most recent chunk ingested."""
    query = f"""
        SELECT MAX(ingested_at) AS last_ts
        FROM `{CHUNKS_TABLE}`
    """
    row = list(client.query(query).result())[0]
    return row["last_ts"]


def get_embedding_failure_rate(client: bigquery.Client) -> float:
    """Compute embedding job failure rate over the past 24 hours."""
    query = f"""
        SELECT
            COUNTIF(status = 'FAILED') / NULLIF(COUNT(*), 0) AS failure_rate
        FROM `{PROJECT_ID}.rag_pipeline.embedding_jobs`
        WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
    """
    try:
        row = list(client.query(query).result())[0]
        return float(row["failure_rate"] or 0.0)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Could not fetch embedding failure rate: %s", exc)
        return 0.0


def get_dlq_backlog(subscription: str) -> int:
    """Return approximate undelivered message count from Pub/Sub DLQ."""
    try:
        subscriber = pubsub_v1.SubscriberClient()
        pull_response = subscriber.pull(
            request={"subscription": subscription, "max_messages": 100}
        )
        return len(pull_response.received_messages)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("DLQ check failed: %s", exc)
        return 0


def classify_status(
    sync_lag: Optional[float],
    failure_rate: float,
    dlq_count: int,
) -> str:
    if (
        (sync_lag is not None and sync_lag > STALE_THRESHOLD_MINUTES * 2)
        or failure_rate > FAILURE_RATE_THRESHOLD * 2
        or dlq_count > DLQ_THRESHOLD * 5
    ):
        return "critical"
    if (
        (sync_lag is not None and sync_lag > STALE_THRESHOLD_MINUTES)
        or failure_rate > FAILURE_RATE_THRESHOLD
        or dlq_count > DLQ_THRESHOLD
    ):
        return "degraded"
    return "healthy"


def build_health_report() -> CorpusHealthReport:
    """Build a full CorpusHealthReport from BigQuery and Pub/Sub."""
    client = bigquery.Client(project=PROJECT_ID)

    stats = get_corpus_stats(client)
    last_ingestion = get_last_ingestion(client)
    failure_rate = get_embedding_failure_rate(client)
    dlq_count = get_dlq_backlog(DLQ_SUBSCRIPTION)

    now = datetime.now(timezone.utc)
    sync_lag: Optional[float] = None
    last_ts_str: Optional[str] = None

    if last_ingestion:
        last_ts_str = last_ingestion.isoformat()
        sync_lag = (now - last_ingestion).total_seconds() / 60

    status = classify_status(sync_lag, failure_rate, dlq_count)

    report = CorpusHealthReport(
        generated_at=now.isoformat(),
        total_chunks=stats["total_chunks"],
        total_documents=stats["total_documents"],
        chunks_by_mime=stats["by_mime"],
        last_ingestion_ts=last_ts_str,
        sync_lag_minutes=round(sync_lag, 2) if sync_lag is not None else None,
        embedding_failure_rate=failure_rate,
        dlq_backlog_count=dlq_count,
        avg_source_age_hours=stats["avg_age_hours"],
        status=status,
    )

    logger.info(
        "RAG pipeline corpus health: %s | lag=%.1f min | failures=%.1f%%",
        status,
        sync_lag or 0,
        failure_rate * 100,
    )
    return report


if __name__ == "__main__":
    import json

    report = build_health_report()
    print(json.dumps(asdict(report), indent=2, default=str))

