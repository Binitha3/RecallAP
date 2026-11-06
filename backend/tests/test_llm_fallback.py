"""Groq fallback ladder tests with a fake OpenAI-compatible client."""

from types import SimpleNamespace
from typing import Any

from backend import llm
from backend.models import DecisionAction


def tool_response(payload: dict[str, Any]) -> Any:
    import json

    call = SimpleNamespace(
        function=SimpleNamespace(name="propose_resolution", arguments=json.dumps(payload))
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]))])


def text_response(content: str) -> Any:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))])


GOOD = {
    "action": "ESCALATE",
    "confidence": 0.4,
    "rationale": "No matching precedent is available. Escalate for human review.",
    "cited_case_ids": [],
}


class SequenceClient:
    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    @property
    def chat(self) -> Any:
        return SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RateLimitError(Exception):
    status_code = 429


def test_primary_429_retries_once_then_returns_tool_result(monkeypatch: Any) -> None:
    client = SequenceClient([RateLimitError("limited"), tool_response(GOOD)])
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)

    result = llm.decide({"invoice": "x"}, [])

    assert result.action == DecisionAction.ESCALATE
    assert len(client.calls) == 2
    assert client.calls[0]["model"] == client.calls[1]["model"]
    assert client.calls[0]["tool_choice"]["function"]["name"] == "propose_resolution"


def test_missing_primary_tool_call_falls_back_to_fallback_model(monkeypatch: Any) -> None:
    no_tool = text_response("I cannot call the tool")
    client = SequenceClient([no_tool, tool_response(GOOD)])
    monkeypatch.setattr(llm, "_get_client", lambda: client)

    result = llm.decide({"invoice": "x"}, [])

    assert result.action == DecisionAction.ESCALATE
    assert len(client.calls) == 2
    assert client.calls[0]["model"] != client.calls[1]["model"]


def test_malformed_fallback_json_uses_failure_decision(monkeypatch: Any) -> None:
    client = SequenceClient([ValueError("tool call failed"), ValueError("tool call failed"), text_response("not json")])
    monkeypatch.setattr(llm, "_get_client", lambda: client)

    result = llm.decide({"invoice": "x"}, [])

    assert result.action == DecisionAction.ESCALATE
    assert result.confidence == 0
    assert "Agent unavailable" in result.rationale
    assert len(client.calls) == 3
    assert "Return JSON only" in client.calls[2]["messages"][-1]["content"]


def test_fallback_json_strips_think_and_validates(monkeypatch: Any) -> None:
    import json

    content = "<think>hidden reasoning</think>\n" + json.dumps(GOOD)
    client = SequenceClient([ValueError("primary"), ValueError("fallback tool"), text_response(content)])
    monkeypatch.setattr(llm, "_get_client", lambda: client)

    result = llm.decide({"invoice": "x"}, [])

    assert result.action == DecisionAction.ESCALATE
    assert len(client.calls) == 3
    assert "tools" not in client.calls[-1]


def test_total_failure_never_raises_and_returns_escalate_zero(monkeypatch: Any) -> None:
    client = SequenceClient([RuntimeError("primary"), RuntimeError("fallback tool"), RuntimeError("json call")])
    monkeypatch.setattr(llm, "_get_client", lambda: client)

    result = llm.decide({"invoice": "x"}, [])

    assert result.action == DecisionAction.ESCALATE
    assert result.confidence == 0
    assert "human review" in result.rationale
