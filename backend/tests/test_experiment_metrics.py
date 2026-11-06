"""Tests for the benchmark's coverage and precision metrics."""

from scripts.run_experiment import build_metrics


def test_metrics_distinguish_touchless_precision_from_coverage() -> None:
    summary = build_metrics([
        {"touchless": True, "correct": True, "unsafe": False, "human_touch": False},
        {"touchless": True, "correct": False, "unsafe": True, "human_touch": False},
        {"touchless": False, "correct": False, "unsafe": False, "human_touch": True},
    ])["summary"]

    assert summary["final_rate"] == 0.3333
    assert summary["touchless_correct"] == 1
    assert summary["touchless_decisions"] == 2
    assert summary["touchless_precision"] == 0.5
    assert summary["unsafe"] == 1


def test_zero_touchless_decisions_have_zero_precision() -> None:
    summary = build_metrics([
        {"touchless": False, "correct": False, "unsafe": False, "human_touch": True},
    ])["summary"]

    assert summary["touchless_decisions"] == 0
    assert summary["touchless_precision"] == 0
