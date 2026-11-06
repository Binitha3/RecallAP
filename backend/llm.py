"""Fail-safe Groq chat-completion ladder for invoice decision proposals."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from backend.config import settings
from backend.models import DecisionAction, ProposedDecision
from backend.prompts import PROPOSE_RESOLUTION_TOOL, build_messages

logger = logging.getLogger(__name__)
_client: OpenAI | None = None
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not configured")
        _client = OpenAI(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
            timeout=20.0,
            max_retries=0,
        )
    return _client


def _is_rate_limit(error: Exception) -> bool:
    return getattr(error, "status_code", None) == 429 or error.__class__.__name__ == "RateLimitError"


def _failure() -> ProposedDecision:
    return ProposedDecision(
        action=DecisionAction.ESCALATE,
        confidence=0,
        rationale="Agent unavailable; routed to human review.",
        cited_case_ids=[],
    )


def _validate_payload(payload: Any) -> ProposedDecision:
    if not isinstance(payload, dict):
        raise ValueError("Model proposal must be a JSON object")
    return ProposedDecision.model_validate(payload)


def _tool_proposal(response: Any) -> ProposedDecision:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise ValueError("Model returned no choices")
    message = getattr(choices[0], "message", None)
    calls = getattr(message, "tool_calls", None) or []
    for call in calls:
        function = getattr(call, "function", None)
        if getattr(function, "name", None) != "propose_resolution":
            continue
        args = getattr(function, "arguments", "")
        return _validate_payload(json.loads(args))
    raise ValueError("Model did not call propose_resolution")


def _json_proposal(response: Any) -> ProposedDecision:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise ValueError("Model returned no choices")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        raise ValueError("Model returned no text")
    content = _THINK_RE.sub("", content).strip()
    match = _JSON_OBJECT_RE.search(content)
    if not match:
        raise ValueError("No JSON object in model response")
    return _validate_payload(json.loads(match.group(0)))


def _chat(client: Any, model: str, messages: list[dict[str, str]], *, use_tool: bool) -> Any:
    params: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
    }
    if use_tool:
        params.update(tools=[PROPOSE_RESOLUTION_TOOL], tool_choice={"type": "function", "function": {"name": "propose_resolution"}})
    return client.chat.completions.create(**params)


def decide(facts: Any, recalled: Any) -> ProposedDecision:
    """Try primary tool call, 429 retry, fallback tool, then fallback JSON; never raise."""
    try:
        client = _get_client()
        primary_messages = build_messages(facts, recalled)
        try:
            response = _chat(client, settings.groq_model_primary, primary_messages, use_tool=True)
            return _tool_proposal(response)
        except Exception as error:
            if _is_rate_limit(error):
                time.sleep(0.25)
                try:
                    response = _chat(client, settings.groq_model_primary, primary_messages, use_tool=True)
                    return _tool_proposal(response)
                except Exception as retry_error:
                    logger.warning("Primary Groq retry failed: %s", retry_error)
            else:
                logger.warning("Primary Groq tool call failed: %s", error)

        fallback_messages = build_messages(facts, recalled)
        try:
            response = _chat(client, settings.groq_model_fallback, fallback_messages, use_tool=True)
            return _tool_proposal(response)
        except Exception as error:
            logger.warning("Fallback Groq tool call failed: %s", error)

        try:
            response = _chat(
                client,
                settings.groq_model_fallback,
                build_messages(facts, recalled, return_json=True),
                use_tool=False,
            )
            return _json_proposal(response)
        except Exception as error:
            logger.warning("Fallback Groq JSON call failed: %s", error)
            return _failure()
    except Exception as error:
        logger.warning("Groq decision unavailable: %s", error)
        return _failure()
