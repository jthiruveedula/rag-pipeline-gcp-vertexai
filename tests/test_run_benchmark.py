"""Tests for src/evaluation/run_benchmark.py"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "experiment": {"name": "test-exp", "description": "unit test"},
    "model": {"name": "gemini-pro", "temperature": 0.0},
    "retrieval": {"top_k": 3, "similarity_threshold": 0.7},
    "evaluation": {
        "faithfulness_threshold": 0.85,
        "p95_latency_threshold_ms": 1200,
        "questions": [
            {"id": "q1", "question": "What is RAG?", "expected_answer": "Retrieval-Augmented Generation"},
        ],
    },
}


def write_config(tmp_path: Path, cfg: dict | None = None) -> Path:
    cfg = cfg or DEFAULT_CONFIG
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg))
    return p


# ---------------------------------------------------------------------------
# Unit tests – argument parsing
# ---------------------------------------------------------------------------

class TestArgumentParsing:
    """Ensure CLI arguments are parsed correctly."""

    def test_required_config_arg(self, tmp_path):
        cfg_path = write_config(tmp_path)
        from src.evaluation.run_benchmark import parse_args
        args = parse_args(["--config", str(cfg_path)])
        assert args.config == str(cfg_path)

    def test_dry_run_flag_default_false(self, tmp_path):
        cfg_path = write_config(tmp_path)
        from src.evaluation.run_benchmark import parse_args
        args = parse_args(["--config", str(cfg_path)])
        assert args.dry_run is False

    def test_dry_run_flag_enabled(self, tmp_path):
        cfg_path = write_config(tmp_path)
        from src.evaluation.run_benchmark import parse_args
        args = parse_args(["--config", str(cfg_path), "--dry-run"])
        assert args.dry_run is True

    def test_threshold_overrides(self, tmp_path):
        cfg_path = write_config(tmp_path)
        from src.evaluation.run_benchmark import parse_args
        args = parse_args([
            "--config", str(cfg_path),
            "--faithfulness-threshold", "0.9",
            "--p95-latency-threshold-ms", "800",
        ])
        assert args.faithfulness_threshold == 0.9
        assert args.p95_latency_threshold_ms == 800


# ---------------------------------------------------------------------------
# Unit tests – config loading
# ---------------------------------------------------------------------------

class TestConfigLoading:
    """Verify config YAML is loaded and validated."""

    def test_load_valid_config(self, tmp_path):
        cfg_path = write_config(tmp_path)
        from src.evaluation.run_benchmark import load_config
        cfg = load_config(str(cfg_path))
        assert cfg["experiment"]["name"] == "test-exp"

    def test_missing_config_raises(self):
        from src.evaluation.run_benchmark import load_config
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("::invalid: yaml: [")
        from src.evaluation.run_benchmark import load_config
        with pytest.raises(Exception):
            load_config(str(bad))


# ---------------------------------------------------------------------------
# Unit tests – dry-run mode
# ---------------------------------------------------------------------------

class TestDryRunMode:
    """Dry-run should not call external APIs and should return a mock scorecard."""

    def test_dry_run_returns_scorecard(self, tmp_path):
        cfg_path = write_config(tmp_path)
        from src.evaluation.run_benchmark import run_benchmark
        result = run_benchmark(str(cfg_path), dry_run=True)
        assert "experiment" in result
        assert "metrics" in result

    def test_dry_run_no_gcp_calls(self, tmp_path):
        cfg_path = write_config(tmp_path)
        with patch("src.evaluation.run_benchmark.vertexai") as mock_vai:
            from src.evaluation.run_benchmark import run_benchmark
            run_benchmark(str(cfg_path), dry_run=True)
            mock_vai.init.assert_not_called()

    def test_dry_run_scorecard_file_written(self, tmp_path):
        cfg_path = write_config(tmp_path)
        os.chdir(tmp_path)
        from src.evaluation.run_benchmark import run_benchmark
        run_benchmark(str(cfg_path), dry_run=True)
        scorecards = list(tmp_path.glob("scorecard*.md"))
        assert len(scorecards) >= 1


# ---------------------------------------------------------------------------
# Unit tests – metric computation
# ---------------------------------------------------------------------------

class TestMetricComputation:
    """Test faithfulness and latency metric helpers."""

    def test_faithfulness_above_threshold_passes(self):
        from src.evaluation.run_benchmark import check_faithfulness_threshold
        assert check_faithfulness_threshold(0.9, 0.85) is True

    def test_faithfulness_below_threshold_fails(self):
        from src.evaluation.run_benchmark import check_faithfulness_threshold
        assert check_faithfulness_threshold(0.7, 0.85) is False

    def test_faithfulness_equal_threshold_passes(self):
        from src.evaluation.run_benchmark import check_faithfulness_threshold
        assert check_faithfulness_threshold(0.85, 0.85) is True

    def test_latency_within_threshold_passes(self):
        from src.evaluation.run_benchmark import check_latency_threshold
        assert check_latency_threshold(1000, 1200) is True

    def test_latency_exceeds_threshold_fails(self):
        from src.evaluation.run_benchmark import check_latency_threshold
        assert check_latency_threshold(1500, 1200) is False


# ---------------------------------------------------------------------------
# Unit tests – scorecard generation
# ---------------------------------------------------------------------------

class TestScorecardGeneration:
    """Scorecard markdown should contain required sections."""

    def test_scorecard_contains_experiment_name(self, tmp_path):
        from src.evaluation.run_benchmark import generate_scorecard
        metrics = {"faithfulness": 0.9, "p95_latency_ms": 900, "passed": True}
        md = generate_scorecard("my-experiment", metrics)
        assert "my-experiment" in md

    def test_scorecard_contains_metrics(self, tmp_path):
        from src.evaluation.run_benchmark import generate_scorecard
        metrics = {"faithfulness": 0.92, "p95_latency_ms": 850, "passed": True}
        md = generate_scorecard("exp", metrics)
        assert "0.92" in md
        assert "850" in md

    def test_scorecard_shows_pass_status(self):
        from src.evaluation.run_benchmark import generate_scorecard
        metrics = {"faithfulness": 0.9, "p95_latency_ms": 900, "passed": True}
        md = generate_scorecard("exp", metrics)
        assert "PASS" in md or "pass" in md.lower()

    def test_scorecard_shows_fail_status(self):
        from src.evaluation.run_benchmark import generate_scorecard
        metrics = {"faithfulness": 0.6, "p95_latency_ms": 1500, "passed": False}
        md = generate_scorecard("exp", metrics)
        assert "FAIL" in md or "fail" in md.lower()


# ---------------------------------------------------------------------------
# Integration tests – full dry-run pipeline
# ---------------------------------------------------------------------------

class TestFullPipelineDryRun:
    """End-to-end dry-run integration test."""

    def test_pipeline_produces_non_empty_metrics(self, tmp_path):
        cfg_path = write_config(tmp_path)
        from src.evaluation.run_benchmark import run_benchmark
        result = run_benchmark(str(cfg_path), dry_run=True)
        assert result["metrics"].get("faithfulness") is not None

    def test_pipeline_with_threshold_override(self, tmp_path):
        cfg_path = write_config(tmp_path)
        from src.evaluation.run_benchmark import run_benchmark
        result = run_benchmark(
            str(cfg_path),
            dry_run=True,
            faithfulness_threshold=0.5,
            p95_latency_threshold_ms=5000,
        )
        assert result["metrics"]["passed"] is True

    def test_pipeline_regression_baseline_not_exceeded(self, tmp_path):
        """Dry-run scores must meet the default thresholds in the config."""
        cfg_path = write_config(tmp_path)
        from src.evaluation.run_benchmark import run_benchmark
        result = run_benchmark(str(cfg_path), dry_run=True)
        metrics = result["metrics"]
        assert metrics["faithfulness"] >= DEFAULT_CONFIG["evaluation"]["faithfulness_threshold"]
        assert metrics["p95_latency_ms"] <= DEFAULT_CONFIG["evaluation"]["p95_latency_threshold_ms"]

