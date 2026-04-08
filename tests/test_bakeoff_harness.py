"""Tests for src/retrieval/bakeoff_harness.py"""
import tempfile
from pathlib import Path

import pytest
import yaml

from src.retrieval import bakeoff_harness


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "experiment": {"name": "bakeoff-test"},
    "retrieval": {"backend": "hybrid_rrf", "hybrid_alpha": 0.5},
}


def write_config(tmp_path: Path, cfg: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg))
    return p


# ---------------------------------------------------------------------------
# Unit tests - metric helpers
# ---------------------------------------------------------------------------

class TestMetricHelpers:
    """Test recall, RR, NDCG calculation."""

    def test_context_recall_at_k_perfect(self):
        retrieved = ["a", "b", "c"]
        ground_truth = ["a", "b", "c"]
        assert bakeoff_harness.context_recall_at_k(retrieved, ground_truth, 3) == 1.0

    def test_context_recall_at_k_partial(self):
        retrieved = ["a", "x", "c"]
        ground_truth = ["a", "b", "c"]
        recall = bakeoff_harness.context_recall_at_k(retrieved, ground_truth, 3)
        assert recall == pytest.approx(2.0 / 3.0)

    def test_context_recall_at_k_none_found(self):
        retrieved = ["x", "y", "z"]
        ground_truth = ["a", "b", "c"]
        assert bakeoff_harness.context_recall_at_k(retrieved, ground_truth, 3) == 0.0

    def test_reciprocal_rank_first_position(self):
        retrieved = ["a", "b", "c"]
        ground_truth = ["a"]
        assert bakeoff_harness.reciprocal_rank(retrieved, ground_truth) == 1.0

    def test_reciprocal_rank_second_position(self):
        retrieved = ["x", "a", "b"]
        ground_truth = ["a"]
        assert bakeoff_harness.reciprocal_rank(retrieved, ground_truth) == 0.5

    def test_reciprocal_rank_none_found(self):
        retrieved = ["x", "y", "z"]
        ground_truth = ["a"]
        assert bakeoff_harness.reciprocal_rank(retrieved, ground_truth) == 0.0

    def test_ndcg_at_k_perfect(self):
        retrieved = ["a", "b", "c"]
        ground_truth = ["a", "b", "c"]
        assert bakeoff_harness.ndcg_at_k(retrieved, ground_truth, 3) == pytest.approx(1.0)

    def test_ndcg_at_k_zero(self):
        retrieved = ["x", "y", "z"]
        ground_truth = ["a", "b", "c"]
        assert bakeoff_harness.ndcg_at_k(retrieved, ground_truth, 3) == 0.0

    def test_p95_latency_single_value(self):
        assert bakeoff_harness.p95_latency([100.0]) == 100.0

    def test_p95_latency_multiple_values(self):
        latencies = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        p95 = bakeoff_harness.p95_latency(latencies)
        assert p95 == 90  # 95th %ile of 10 values


# ---------------------------------------------------------------------------
# Unit tests - dry-run harness
# ---------------------------------------------------------------------------

class TestBakeoffHarnessDryRun:
    """Test the full bake-off harness with dry-run mode."""

    def test_dry_run_returns_results(self, tmp_path):
        cfg_path = write_config(tmp_path, DEFAULT_CONFIG)
        output = bakeoff_harness.run_bakeoff(
            config_path=str(cfg_path),
            eval_dataset_path="",
            k=5,
            dry_run=True,
        )
        assert "results" in output
        assert "comparison_table" in output
        assert "recommended_backend" in output

    def test_dry_run_all_backends(self, tmp_path):
        cfg_path = write_config(tmp_path, DEFAULT_CONFIG)
        output = bakeoff_harness.run_bakeoff(
            config_path=str(cfg_path),
            eval_dataset_path="",
            k=5,
            backends=["bq_vector", "vertex_ai_search", "hybrid_rrf"],
            dry_run=True,
        )
        assert len(output["results"]) == 3

    def test_dry_run_single_backend(self, tmp_path):
        cfg_path = write_config(tmp_path, DEFAULT_CONFIG)
        output = bakeoff_harness.run_bakeoff(
            config_path=str(cfg_path),
            eval_dataset_path="",
            k=5,
            backends=["bq_vector"],
            dry_run=True,
        )
        assert len(output["results"]) == 1
        assert output["results"][0]["backend"] == "bq_vector"

    def test_dry_run_produces_table(self, tmp_path):
        cfg_path = write_config(tmp_path, DEFAULT_CONFIG)
        output = bakeoff_harness.run_bakeoff(
            config_path=str(cfg_path),
            eval_dataset_path="",
            k=5,
            dry_run=True,
        )
        table = output["comparison_table"]
        assert "Backend" in table
        assert "Recall@k" in table
        assert "MRR" in table

    def test_dry_run_recommendation_is_valid_backend(self, tmp_path):
        cfg_path = write_config(tmp_path, DEFAULT_CONFIG)
        output = bakeoff_harness.run_bakeoff(
            config_path=str(cfg_path),
            eval_dataset_path="",
            k=5,
            dry_run=True,
        )
        assert output["recommended_backend"] in bakeoff_harness.BACKENDS


# ---------------------------------------------------------------------------
# Integration test - CLI arg parsing
# ---------------------------------------------------------------------------

class TestCLIParsing:
    """Test CLI argument parsing."""

    def test_required_config_arg(self, tmp_path):
        cfg = write_config(tmp_path, DEFAULT_CONFIG)
        args = bakeoff_harness.parse_args(["--config", str(cfg)])
        assert args.config == str(cfg)

    def test_default_k_is_5(self, tmp_path):
        cfg = write_config(tmp_path, DEFAULT_CONFIG)
        args = bakeoff_harness.parse_args(["--config", str(cfg)])
        assert args.k == 5

    def test_override_k(self, tmp_path):
        cfg = write_config(tmp_path, DEFAULT_CONFIG)
        args = bakeoff_harness.parse_args(["--config", str(cfg), "--k", "10"])
        assert args.k == 10

    def test_default_eval_dataset_path(self, tmp_path):
        cfg = write_config(tmp_path, DEFAULT_CONFIG)
        args = bakeoff_harness.parse_args(["--config", str(cfg)])
        assert args.eval_dataset == "eval_dataset.jsonl"

    def test_override_backends(self, tmp_path):
        cfg = write_config(tmp_path, DEFAULT_CONFIG)
        args = bakeoff_harness.parse_args([
            "--config", str(cfg),
            "--backends", "bq_vector", "hybrid_rrf",
        ])
        assert args.backends == ["bq_vector", "hybrid_rrf"]

    def test_dry_run_flag(self, tmp_path):
        cfg = write_config(tmp_path, DEFAULT_CONFIG)
        args = bakeoff_harness.parse_args(["--config", str(cfg), "--dry-run"])
        assert args.dry_run is True

