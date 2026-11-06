"""System instructions and tool schema for the RecallAP decision model."""

from __future__ import annotations

from typing import Any

from backend.models import DecisionAction


SYSTEM_PROMPT = """You propose actions for Accounts Payable invoice exceptions.
Base decisions only on the invoice facts and recalled memories supplied by the application.
Cite only CASE ids that appear in recalled memories. A precedent applies only when its vendor,
exception type, and stated range match the current invoice. With no applicable human-decided
precedent, choose ESCALATE. Never approve changed bank details. Be concise and explain the
evidence in 2-4 sentences. Confidence calibration: no precedent means ESCALATE; exactly one
matching human-decided case means confidence at most 0.75; two or more matching human cases
within range may support confidence 0.85-0.95. Return only the requested structured proposal."""


PROPOSE_RESOLUTION_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "propose_resolution",
        "description": "Propose a guarded action for an AP invoice exception.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [action.value for action in DecisionAction],
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
                "cited_case_ids": {"type": "array", "items": {"type": "string"}},
                "draft_message": {"type": "string"},
            },
            "required": ["action", "confidence", "rationale", "cited_case_ids"],
            "additionalProperties": False,
        },
    },
}


def build_messages(facts: Any, recalled: Any, *, return_json: bool = False) -> list[dict[str, str]]:
    """Serialize supplied evidence for the model without adding hidden context."""
    import json

    payload = {"invoice_facts": facts, "recalled_memories": recalled}
    user_content = json.dumps(payload, ensure_ascii=False, default=str)
    if return_json:
        user_content += "\nReturn JSON only with action, confidence, rationale, cited_case_ids, and optional draft_message."
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
