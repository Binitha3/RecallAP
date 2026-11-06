"""Load the teammate-owned experiment results with safe empty defaults."""

from __future__ import annotations

import json
from typing import Any

from backend.models import MetricsResponse
from backend.store import APP_ROOT

EXPERIMENT_RESULTS = APP_ROOT / "data" / "experiment_results.json"


def _empty() -> MetricsResponse:
    return MetricsResponse(points=[], summary={})


def get_metrics(memory: str = "on") -> MetricsResponse:
    """Return experiment results for memory-on/off from the shared data file."""
    try:
        with EXPERIMENT_RESULTS.open("r", encoding="utf-8") as stream:
            data: Any = json.load(stream)
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()

    key = "memory_on" if memory == "on" else "memory_off"
    selected: Any = data.get(key, data.get(memory))
    if selected is None and data.get("memory") == memory:
        selected = data
    if not isinstance(selected, dict):
        return _empty()
    try:
        summary = selected.get("summary", {})
        if not isinstance(summary, dict):
            summary = {}
        summary = {
            **summary,
            "processed": summary.get("processed", summary.get("invoices", 0)),
            "touchless": summary.get("touchless", summary.get("touchless_correct", 0)),
            "human_touch": summary.get("human_touch", summary.get("human_touches", 0)),
            "auto_correct": summary.get("auto_correct", summary.get("final_rate", 0)),
        }
        return MetricsResponse.model_validate(
            {"points": selected.get("points", []), "summary": summary}
        )
    except Exception:
        return _empty()
