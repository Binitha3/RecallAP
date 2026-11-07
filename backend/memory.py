"""Hindsight retain, recall, and reflect adapter."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any

from backend.config import settings
from backend.models import RecalledMemory

logger = logging.getLogger(__name__)
CASE_ID_RE = re.compile(r"CASE-\d{4}")


class HindsightSearchError(RuntimeError):
    """Raised when Hindsight could not execute a memory search."""


class HindsightRecallError(RuntimeError):
    """Raised when precedent recall fails instead of returning an empty bank."""


class HindsightCaseCounterError(RuntimeError):
    """Raised when the bank could not be scanned to avoid reusing a CASE ID."""

def _get_client() -> Any:
    """Create a fresh client per operation; a shared one breaks across threads."""
    if not settings.hindsight_base_url or not settings.hindsight_api_key:
        raise RuntimeError("Hindsight Cloud credentials are not configured")
    from hindsight_client import Hindsight

    return Hindsight(
        base_url=settings.hindsight_base_url,
        api_key=settings.hindsight_api_key,
        timeout=15.0,
    )


def _close_client(client: Any) -> None:
    """Close an SDK client on the same worker thread that used its sync methods."""
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logger.debug("Could not close Hindsight client cleanly", exc_info=True)


def create_bank(bank_id: str) -> None:
    """Provision an isolated Hindsight bank before the app starts using it."""
    if not bank_id:
        raise ValueError("bank_id is required")
    client = _get_client()
    try:
        client.create_bank(
            bank_id=bank_id,
            name="RecallAP experiment",
            background="Memory for invoice exception decisions and human-approved precedents.",
        )
    finally:
        _close_client(client)


def _metadata_case_id(record: Any) -> str | None:
    """Read a validated CASE ID from Hindsight's fact metadata, if present."""
    metadata = getattr(record, "metadata", None)
    if isinstance(metadata, dict):
        value = metadata.get("case_id")
        if isinstance(value, str) and CASE_ID_RE.fullmatch(value):
            return value
    return None


def _source_chunk(record: Any, response: Any) -> str:
    """Get the original retained chunk associated with a recalled fact."""
    chunks = getattr(response, "chunks", None)
    chunk_id = getattr(record, "chunk_id", None)
    if not isinstance(chunks, dict) or not chunk_id:
        return ""
    chunk = chunks.get(chunk_id)
    if isinstance(chunk, dict):
        value = chunk.get("text", "")
    else:
        value = getattr(chunk, "text", "")
    return value if isinstance(value, str) else ""


def _guess_memory_type(text: str, context: str | None, case_id: str | None) -> str:
    """Use the retain context where available and conservative text cues otherwise."""
    if context in {"vendor_profile", "resolution_case", "approver_preference"}:
        return context
    if case_id or CASE_ID_RE.search(text):
        return "resolution_case"
    lowered = text.casefold()
    if any(cue in lowered for cue in ("prefers", "approver", "never auto-approves", "ap manager")):
        return "approver_preference"
    return "vendor_profile"


def _parse_recalled(response: Any) -> list[RecalledMemory]:
    """Convert a Hindsight response into citation-safe typed memories."""
    records = getattr(response, "results", None) or []
    parsed: list[RecalledMemory] = []
    for record in records:
        fact_text = getattr(record, "text", "")
        fact_text = fact_text if isinstance(fact_text, str) else str(fact_text)
        source_text = _source_chunk(record, response)
        metadata_id = _metadata_case_id(record)
        source_id_match = CASE_ID_RE.search(source_text)
        case_id = metadata_id or (source_id_match.group(0) if source_id_match else None)
        # A case source chunk retains the signed variance, range, approver and decision tag.
        text = source_text if case_id and source_text else fact_text
        context = getattr(record, "context", None)
        memory_type = _guess_memory_type(text, context if isinstance(context, str) else None, case_id)
        parsed.append(RecalledMemory(case_id=case_id, memory_type=memory_type, text=text))
    return parsed


def retain_case(
    bank_id: str,
    text: str,
    context: str = "resolution_case",
    timestamp: str | datetime | None = None,
) -> bool:
    """Retain one memory, attaching its CASE ID as searchable metadata when present."""
    if not bank_id:
        raise ValueError("bank_id is required")
    metadata: dict[str, str] = {}
    match = CASE_ID_RE.search(text)
    if match:
        metadata["case_id"] = match.group(0)
    for attempt in range(3):
        client = None
        try:
            client = _get_client()
            options: dict[str, Any] = {"bank_id": bank_id, "content": text, "context": context}
            if timestamp is not None:
                options["timestamp"] = timestamp
            if metadata:
                options["metadata"] = metadata
            response = client.retain(**options)
            if getattr(response, "success", False) is not True:
                raise RuntimeError("Hindsight retain response reported success=false")
            items_count = getattr(response, "items_count", 1)
            if isinstance(items_count, int) and items_count < 1:
                raise RuntimeError("Hindsight retain response accepted zero items")
            return True
        except Exception as exc:
            logger.warning("Hindsight retain failed (attempt %d/3): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
        finally:
            if client is not None:
                _close_client(client)
    return False


def highest_case_number(bank_id: str) -> int:
    """Find the largest CASE number already stored in a bank, including legacy text."""
    if not bank_id:
        raise ValueError("bank_id is required")
    highest = 0
    offset = 0
    page_size = 100
    try:
        client = _get_client()
        while True:
            page = client.list_memories(bank_id=bank_id, limit=page_size, offset=offset)
            items = getattr(page, "items", None) or []
            for item in items:
                metadata = getattr(item, "metadata", None)
                if isinstance(metadata, dict):
                    case_id = metadata.get("case_id")
                    match = CASE_ID_RE.fullmatch(case_id) if isinstance(case_id, str) else None
                    if match:
                        highest = max(highest, int(match.group(0)[5:]))
                searchable = " ".join(
                    str(value)
                    for value in (getattr(item, "text", ""), getattr(item, "entities", ""))
                    if value
                )
                for case_id in CASE_ID_RE.findall(searchable):
                    highest = max(highest, int(case_id[5:]))
            offset += len(items)
            total = getattr(page, "total", offset)
            if not items or offset >= total:
                _close_client(client)
                return highest
    except Exception as exc:
        if "client" in locals():
            _close_client(client)
        logger.warning("Could not scan Hindsight bank for existing CASE IDs: %s", exc)
        raise HindsightCaseCounterError("Could not verify existing CASE IDs in Hindsight") from exc


def recall_for(
    bank_id: str,
    vendor_name: str,
    exc_type: str,
    detail: str,
    limit: int = 6,
) -> list[RecalledMemory]:
    """Recall precedent, vendor-profile, and approver-preference memories."""
    if not bank_id:
        raise ValueError("bank_id is required")
    queries = (
        f"{vendor_name} {exc_type} {detail}",
        f"What human-approved precedents and approved tolerance ranges exist for {vendor_name} {exc_type}? Include reusable cases and resolutions.",
        f"What are the known billing quirks and payment terms of {vendor_name}?",
        f"Approver preferences and rules for {exc_type} invoices",
    )
    output: list[RecalledMemory] = []
    seen_text: set[str] = set()
    seen_case_ids: set[str] = set()
    try:
        client = _get_client()
        for query in queries:
            response = client.recall(
                bank_id=bank_id,
                query=query,
                include_chunks=True,
                max_tokens=4096,
                max_chunk_tokens=4096,
            )
            for memory in _parse_recalled(response):
                if memory.case_id:
                    if memory.case_id in seen_case_ids:
                        continue
                    seen_case_ids.add(memory.case_id)
                elif memory.text in seen_text:
                    continue
                seen_text.add(memory.text)
                output.append(memory)
        # Later, broader precedent queries can surface human cases omitted by
        # the first detailed query. Collect before capping, then reserve slots
        # for resolution evidence ahead of lower-priority profile context.
        priority = {"resolution_case": 0, "vendor_profile": 1, "approver_preference": 2}
        output.sort(key=lambda item: priority.get(item.memory_type, 3))
        return output[: max(1, limit)]
    except Exception as exc:
        logger.warning("Hindsight recall failed: %s", exc, exc_info=True)
        raise HindsightRecallError("Hindsight memory recall failed") from exc
    finally:
        if "client" in locals():
            _close_client(client)


def reflect_vendor(bank_id: str, vendor_name: str) -> str:
    """Return Hindsight's contextual vendor briefing, or an empty string on failure."""
    if not bank_id:
        raise ValueError("bank_id is required")
    try:
        client = _get_client()
        response = client.reflect(
            bank_id=bank_id,
            query=f"What should I know about {vendor_name}?",
        )
        text = getattr(response, "text", "")
        return text if isinstance(text, str) else str(response)
    except Exception as exc:
        logger.warning("Hindsight reflect failed: %s", exc)
        return ""
    finally:
        if "client" in locals():
            _close_client(client)


def search_memory(bank_id: str, query: str, limit: int = 20) -> list[RecalledMemory]:
    """Search the active bank and return parsed, deduplicated memories."""
    if not bank_id:
        raise ValueError("bank_id is required")
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("q must not be empty")
    try:
        client = _get_client()
        response = client.recall(
            bank_id=bank_id,
            query=cleaned_query,
            include_chunks=True,
            max_tokens=4096,
            max_chunk_tokens=4096,
        )
        output: list[RecalledMemory] = []
        seen: set[tuple[str | None, str]] = set()
        for item in _parse_recalled(response):
            key = (item.case_id, item.text)
            if key in seen:
                continue
            seen.add(key)
            output.append(item)
            if len(output) >= max(1, min(limit, 100)):
                break
        return output
    except Exception as exc:
        logger.warning("Hindsight search failed: %s", exc, exc_info=True)
        raise HindsightSearchError("Hindsight memory search failed") from exc
    finally:
        if "client" in locals():
            _close_client(client)
