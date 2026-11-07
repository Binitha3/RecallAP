"""Memory adapter tests using response-shaped fake Hindsight results."""

from types import SimpleNamespace
from typing import Any

from backend import memory


CASE_TEXT = (
    'CASE-0001 | 2025-03-12 | Vendor: Deccan Freight Lines (V-1004) | '
    'Exception: PRICE_MISMATCH. Invoice INV-2025-0731 billed Rs. 104,000 '
    'against PO Rs. 100,000 (+4.0%). Resolution: APPROVED by Priya Nair. '
    'Applies to: this vendor, price variance up to 5%. Decided by: human.'
)


def fake_result(
    text: str,
    *,
    metadata: dict[str, str] | None = None,
    context: str | None = "resolution_case",
    chunk_id: str | None = "chunk-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        type="world",
        metadata=metadata or {},
        context=context,
        chunk_id=chunk_id,
    )


def fake_response(records: list[Any], chunk_text: str = CASE_TEXT) -> SimpleNamespace:
    chunks = {"chunk-1": SimpleNamespace(text=chunk_text, truncated=False)}
    return SimpleNamespace(results=records, chunks=chunks)


def test_parse_uses_metadata_id_and_verbatim_source_chunk() -> None:
    response = fake_response(
        [fake_result("A price variance up to 5% was approved.", metadata={"case_id": "CASE-0001"})]
    )

    memories = memory._parse_recalled(response)

    assert len(memories) == 1
    assert memories[0].case_id == "CASE-0001"
    assert memories[0].memory_type == "resolution_case"
    assert memories[0].text == CASE_TEXT
    assert "up to 5%" in memories[0].text


def test_parse_falls_back_to_case_id_in_source_chunk() -> None:
    response = fake_response([fake_result("The vendor accepts surcharges.", metadata={})])

    memories = memory._parse_recalled(response)

    assert memories[0].case_id == "CASE-0001"
    assert memories[0].text == CASE_TEXT


def test_parse_uses_context_to_guess_non_case_memory_types() -> None:
    records = [
        fake_result("Vendor bills fuel surcharges.", context="vendor_profile", chunk_id=None),
        fake_result("Priya prefers verification.", context="approver_preference", chunk_id=None),
    ]
    response = SimpleNamespace(results=records, chunks={})

    memories = memory._parse_recalled(response)

    assert [item.memory_type for item in memories] == ["vendor_profile", "approver_preference"]
    assert all(item.case_id is None for item in memories)


def test_recall_runs_four_queries_dedupes_and_caps_results(monkeypatch: Any) -> None:
    case = fake_result("Fact without case identifier", metadata={"case_id": "CASE-0001"})
    profile = fake_result("Deccan Freight Lines is on Net 30 terms.", metadata={}, context="vendor_profile", chunk_id=None)
    preference = fake_result("Priya prefers contract checks.", metadata={}, context="approver_preference", chunk_id=None)

    class FakeClient:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def recall(self, *, bank_id: str, query: str, **kwargs: Any) -> Any:
            assert bank_id == "bank-a"
            assert kwargs["include_chunks"] is True
            self.queries.append(query)
            return SimpleNamespace(results=[case, profile, preference], chunks={"chunk-1": SimpleNamespace(text=CASE_TEXT)})

    fake_client = FakeClient()
    monkeypatch.setattr(memory, "_get_client", lambda: fake_client)

    result = memory.recall_for("bank-a", "Deccan Freight Lines", "PRICE_MISMATCH", "surcharge", limit=6)

    assert len(fake_client.queries) == 4
    assert "human-approved precedents" in fake_client.queries[1]
    assert len(result) == 3
    assert result[0].case_id == "CASE-0001"
    assert [item.memory_type for item in result] == [
        "resolution_case",
        "vendor_profile",
        "approver_preference",
    ]


def test_recall_prioritizes_cases_from_broad_query_before_applying_limit(monkeypatch: Any) -> None:
    def case(case_id: str) -> Any:
        source = CASE_TEXT.replace("CASE-0001", case_id)
        record = fake_result("approved resolution", metadata={"case_id": case_id})
        return record, SimpleNamespace(text=source)

    first_cases = [case("CASE-0003"), case("CASE-0002")]
    late_case = case("CASE-0001")
    profiles = [
        (fake_result(f"profile {index}", context="vendor_profile", chunk_id=None), None)
        for index in range(4)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def recall(self, **kwargs: Any) -> Any:
            self.calls += 1
            pairs = first_cases + profiles if self.calls == 1 else [late_case] if self.calls == 2 else []
            records = [record for record, _ in pairs]
            chunks = {
                f"chunk-{index}": chunk
                for index, (_, chunk) in enumerate(pairs)
                if chunk is not None
            }
            for index, (record, chunk) in enumerate(pairs):
                if chunk is not None:
                    record.chunk_id = f"chunk-{index}"
            return SimpleNamespace(results=records, chunks=chunks)

    fake_client = FakeClient()
    monkeypatch.setattr(memory, "_get_client", lambda: fake_client)

    result = memory.recall_for("bank-a", "Deccan Freight Lines", "PRICE_MISMATCH", "variance", limit=3)

    assert [item.case_id for item in result] == ["CASE-0003", "CASE-0002", "CASE-0001"]


def test_recall_fails_soft(monkeypatch: Any) -> None:
    class FailingClient:
        def recall(self, **kwargs: Any) -> Any:
            raise TimeoutError("offline")

    monkeypatch.setattr(memory, "_get_client", lambda: FailingClient())
    assert memory.recall_for("bank-a", "Vendor", "PRICE_MISMATCH", "variance") == []


def test_retain_retries_three_times_and_attaches_case_id(monkeypatch: Any) -> None:
    class RetryClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def retain(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            if len(self.calls) < 3:
                raise TimeoutError("retry")
            return SimpleNamespace(success=True, items_count=1)

    fake_client = RetryClient()
    monkeypatch.setattr(memory, "_get_client", lambda: fake_client)
    monkeypatch.setattr(memory.time, "sleep", lambda _: None)

    assert memory.retain_case("bank-a", CASE_TEXT) is True
    assert len(fake_client.calls) == 3
    assert fake_client.calls[-1]["metadata"] == {"case_id": "CASE-0001"}


def test_retain_returns_false_after_three_failures(monkeypatch: Any) -> None:
    class FailingClient:
        def retain(self, **kwargs: Any) -> None:
            raise TimeoutError("offline")

    monkeypatch.setattr(memory, "_get_client", lambda: FailingClient())
    monkeypatch.setattr(memory.time, "sleep", lambda _: None)
    assert memory.retain_case("bank-a", CASE_TEXT) is False


def test_retain_does_not_report_success_when_hindsight_rejects_it(monkeypatch: Any) -> None:
    class RejectingClient:
        def __init__(self) -> None:
            self.calls = 0

        def retain(self, **kwargs: Any) -> Any:
            self.calls += 1
            return SimpleNamespace(success=False, items_count=0)

    client = RejectingClient()
    monkeypatch.setattr(memory, "_get_client", lambda: client)
    monkeypatch.setattr(memory.time, "sleep", lambda _: None)

    assert memory.retain_case("bank-a", CASE_TEXT) is False
    assert client.calls == 3


def test_reflect_returns_response_text(monkeypatch: Any) -> None:
    class FakeClient:
        def reflect(self, **kwargs: Any) -> Any:
            assert kwargs == {
                "bank_id": "bank-a",
                "query": "What should I know about Deccan Freight Lines?",
            }
            return SimpleNamespace(text="Net 30; fuel surcharge up to 5%.")

    monkeypatch.setattr(memory, "_get_client", lambda: FakeClient())
    assert memory.reflect_vendor("bank-a", "Deccan Freight Lines") == "Net 30; fuel surcharge up to 5%."


def test_search_memory_queries_and_caps_results(monkeypatch: Any) -> None:
    class FakeClient:
        def recall(self, **kwargs: Any) -> Any:
            assert kwargs["bank_id"] == "bank-a"
            assert kwargs["query"] == "fuel surcharge"
            return fake_response([fake_result("A fact", metadata={"case_id": "CASE-0001"})])

    monkeypatch.setattr(memory, "_get_client", lambda: FakeClient())
    results = memory.search_memory("bank-a", " fuel surcharge ", limit=1)
    assert len(results) == 1
    assert results[0].case_id == "CASE-0001"


def test_search_memory_surfaces_hindsight_failures(monkeypatch: Any) -> None:
    class FailingClient:
        def recall(self, **kwargs: Any) -> Any:
            raise TimeoutError("offline")

    monkeypatch.setattr(memory, "_get_client", lambda: FailingClient())

    try:
        memory.search_memory("bank-a", "Deccan")
    except memory.HindsightSearchError as exc:
        assert str(exc) == "Hindsight memory search failed"
    else:
        raise AssertionError("Hindsight search failure should not look like an empty result")


def test_highest_case_number_scans_metadata_and_legacy_text(monkeypatch: Any) -> None:
    class FakeClient:
        def list_memories(self, *, bank_id: str, limit: int, offset: int) -> Any:
            assert bank_id == "bank-a"
            if offset == 0:
                items = [
                    SimpleNamespace(
                        metadata={"case_id": "CASE-0004"},
                        text="Legacy source includes CASE-0006.",
                        entities="",
                    )
                ]
                return SimpleNamespace(items=items, total=2)
            return SimpleNamespace(
                items=[SimpleNamespace(metadata={}, text="", entities="CASE-0005")],
                total=2,
            )

    monkeypatch.setattr(memory, "_get_client", lambda: FakeClient())

    assert memory.highest_case_number("bank-a") == 6
