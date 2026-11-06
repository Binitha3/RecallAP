"""Metrics reader tests with teammate-owned experiment fixtures."""

import json
from pathlib import Path

from backend import metrics


def test_reads_selected_memory_mode(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "experiment_results.json"
    path.write_text(
        json.dumps(
            {
                "memory_on": {
                    "points": [{"n": 1, "rolling_rate": 0.5, "auto_correct": 1, "human_touch": 0}],
                    "summary": {"processed": 1, "touchless": 1, "human_touch": 0, "auto_correct": 1},
                },
                "memory_off": {"points": [], "summary": {"processed": 0}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(metrics, "EXPERIMENT_RESULTS", path)

    result = metrics.get_metrics("on")

    assert result.points[0].rolling_rate == 0.5
    assert result.summary.touchless == 1


def test_missing_or_invalid_metrics_return_empty(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "missing.json"
    monkeypatch.setattr(metrics, "EXPERIMENT_RESULTS", path)
    assert metrics.get_metrics("off").points == []
    path.write_text("{invalid", encoding="utf-8")
    assert metrics.get_metrics("off").summary.processed == 0
