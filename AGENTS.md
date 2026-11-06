# RecallAP — Accounts Payable Exception Agent

## What it is
RecallAP is an Accounts Payable exception agent for a hackathon (HackwithHyderabad 3.0). It checks an invoice against its purchase order (PO) and goods receipt (GRN), detects exceptions with plain code, recalls similar past resolutions from Hindsight memory (by Vectorize), asks an LLM to propose an action, runs that proposal through hard guardrails in code, then auto-resolves or escalates to a human. Every human decision is retained back into Hindsight so the next similar case is easier.

## Scope
Member 1 (Backend and Agent) owns only `backend/`, `scripts/smoke_hindsight.py`, `frontend/mock/*.json`, `requirements.txt`, `.env.example`, `.gitignore`, and the technical README sections. Do not build the frontend UI, dataset generator, or experiment runner (other teammates own those). Out of scope: OCR, login, real ERP, payments, email sending.

## Stack
Python 3.11, FastAPI, pydantic v2, uvicorn, pytest. LLM via Groq: `openai/gpt-oss-120b` (primary), `qwen/qwen3-32b` (fallback). Memory: `hindsight-client` (Hindsight Cloud). FastAPI serves `frontend/` as static files at `/`. Use plain `def` routes (the clients are synchronous) and a lock around the store and case counter.

## Layout
```text
backend/  main.py config.py models.py detectors.py memory.py llm.py prompts.py agent.py policy.py store.py metrics.py
          tests/  test_detectors.py test_policy.py test_llm_fallback.py test_memory_parsing.py
scripts/  smoke_hindsight.py
frontend/mock/  invoices.json invoice_detail.json decision_cold.json decision_warm.json decision_guardrail.json metrics_on.json metrics_off.json
.env.example  requirements.txt  README.md  .gitignore (excludes .env, .venv, logs/)
```

## Environment
`GROQ_API_KEY`, `GROQ_MODEL_PRIMARY`, `GROQ_MODEL_FALLBACK`, `HINDSIGHT_BASE_URL`, `HINDSIGHT_API_KEY`, `BANK_ID`, `RETAIN_AGENT_DECISIONS`.

## API contract (field names are frozen)
- `GET /api/health` -> `{status, hindsight_ok, llm_ok}`
- `GET /api/invoices (?status=)` -> `[{id, vendor_name, amount, currency, status, exception_types[]}]`
- `GET /api/invoices/{id}` -> `{invoice, po, grn, vendor, exceptions[]}`
- `POST /api/invoices/{id}/process (?memory=on|off, ?bank=)` -> Decision object
- `POST /api/invoices/{id}/resolve (?bank=)` body `{action, note, approver}` -> `{case_id, status, retained: true}`
- `GET /api/memory/search (?q=, ?bank=)` -> `[{case_id?, type, text}]`
- `GET /api/vendors/{id}/briefing` -> `{vendor_id, briefing_text}`
- `GET /api/metrics (?memory=on|off)` -> `{points:[{n, rolling_rate, auto_correct, human_touch}], summary{}}` (serves data/experiment_results.json)
- `POST /api/demo/reset` body `{clear_memory: bool}` -> `{ok: true}`

Decision object: `{invoice_id, memory_used, exceptions:[{type, detail, severity, variance_pct}], recalled:[{case_id, memory_type, text}], decision:{action, confidence, rationale, cited_case_ids, draft_message}, guardrails:{passed, triggered[], downgraded_from}, final_status, latency_ms}`. `final_status`: `AUTO_RESOLVED | VENDOR_QUERY | REJECTED | NEEDS_REVIEW | CLEAN` (first three are touchless). LLM actions: `AUTO_APPROVE`, `AUTO_APPROVE_WITH_NOTE`, `REQUEST_VENDOR_INFO`, `REJECT`, `ESCALATE`. Data shapes: vendor `{id, name, gstin, bank_account (masked XXXX4821), payment_terms, category, currency}`; PO `{po_number, vendor_id, total, currency, lines[{item, qty, unit_price}]}`; GRN `{po_number, received[{item, qty}]}`; invoice `{id, vendor_id, po_number, invoice_number, date, currency, gstin, gst_rate, bank_account, lines[], total, seq}`.

## Detectors
Pure functions, `run_all(inv, po, grn, vendor)`. `PRICE_MISMATCH` (|invoice - PO| / PO > 0.5%; keep the SIGN in `variance_pct`; severity low under 5%, else medium/high), `QTY_MISMATCH` (billed vs GRN received), `MISSING_PO` (empty or unknown), `GSTIN_MISMATCH` (invoice vs vendor master), `TAX_RATE_MISMATCH` (GST rate differs from expected for the category), `CURRENCY_MISMATCH` (invoice vs PO), `DUPLICATE_INVOICE` (same vendor + invoice number, or same vendor + amount + date within 7 days), `BANK_DETAILS_CHANGED` (critical). Return exceptions ordered by severity descending (critical, high, medium, low), ties broken by the order above.

## Guardrails
In `policy.py`; each needs a unit test.
- G1 any `BANK_DETAILS_CHANGED` -> `ESCALATE` always.
- G2 any non-`ESCALATE` action needs a cited case with the same vendor AND same exception type AND at least one human-decided case (agent-decided cases alone never count); exception: `REJECT` of a pure `DUPLICATE_INVOICE`.
- G3 variance outside the precedent's "up to X%" range -> `ESCALATE` (a 5% precedent never covers 9%); if no range is parseable, in range only within 1 percentage point of the precedent's variance.
- G4 invoice total above Rs. 2,50,000 -> `ESCALATE` (approver memory may only lower this limit).
- G5 confidence below 0.80 -> `ESCALATE`.
- G6 strip cited CASE ids not present in the recalled results; if none remain, G2 applies.
- G7 more than one exception type with only some having precedent -> `ESCALATE`.
- G8 LLM failure or invalid output -> `ESCALATE` with note `agent unavailable; human review`.
The LLM can never bypass these. Range parsing must handle percent (`up to 5%`) and amounts (`below Rs. 50,000`, Indian grouping `Rs. 2,50,000`).

## LLM ladder
In `llm.py`, never raises. 1) primary model, forced tool call `propose_resolution`, temperature 0.1. 2) same model, retry once after a short sleep (429). 3) fallback model, strip `<think>...</think>`. 4) fallback model without tools, append `Return JSON only`, regex the first `{...}`, validate with pydantic. 5) return `ESCALATE`, confidence 0, rationale `Agent unavailable; routed to human review.` `draft_message` is optional (default `""`). Tool schema `propose_resolution`: action (enum above), confidence 0.0-1.0, rationale (2-4 sentences citing CASE ids), `cited_case_ids[]`, `draft_message`.

System prompt rules: base decisions only on given facts and recalled memories; cite only recalled CASE ids; a precedent applies only if vendor and exception type match and the case is within its stated range; no memories -> `ESCALATE`; never approve changed bank details; be concise. Confidence calibration: no precedent -> escalate; exactly one matching human case -> confidence at most 0.75; two or more matching human cases within range -> 0.85 to 0.95.

## Memory
In `memory.py`. Three kinds with a context label: `vendor_profile`, `resolution_case`, `approver_preference`. Every function takes `bank_id` (no global bank). Case counter is per bank and thread-safe: `CASE-0001`, `CASE-0002`...; seeded demo memories start at `CASE-9001`. Retain cases as one self-contained sentence in this format: `CASE-0007 | 2025-03-12 | Vendor: Deccan Freight Lines (V-1004) | Exception: PRICE_MISMATCH. Invoice INV-2025-0731 billed Rs. 104,000 against PO Rs. 100,000 (+4.0%). Resolution: APPROVED by Priya Nair (AP Manager). Reason given: "Fuel surcharge up to 5% is covered by the contract amendment." Applies to: this vendor, price variance up to 5%. Decided by: human. Outcome: paid.` Always include vendor name AND id, exception type, an `Applies to ... up to X%` (or `below Rs. N`) phrase, and `Decided by: human|agent`. `recall_for(bank_id, vendor_name, exc_type, detail)` runs three queries (precedent: `"{vendor} {type} {detail}"`; vendor quirks: `"What are the known billing quirks and payment terms of {vendor}?"`; approver: `"Approver preferences and rules for {type} invoices"`), dedupes by text, keeps at most 6, extracts ids with `CASE-\d{4}`, guesses `memory_type`. Retain retries 3 times with backoff; recall fails soft to `[]`. `reflect_vendor` powers the briefing endpoint.

## Agent pipeline
In `agent.py`. Load context -> detect (none = `CLEAN`) -> recall (only if memory on, using the first exception) -> LLM decide -> guardrails -> set status (`AUTO_APPROVE*`/`AUTO_APPROVE_WITH_NOTE` -> `AUTO_RESOLVED`, `REQUEST_VENDOR_INFO` -> `VENDOR_QUERY`, `REJECT` -> `REJECTED`, else `NEEDS_REVIEW`) -> retain own touchless decisions only if `RETAIN_AGENT_DECISIONS` is on (tagged `Decided by: agent`). Memory OFF means only "skip recall"; everything else is identical so the ON/OFF experiment is fair. Log every decision as one JSON line in `logs/decisions.jsonl`. If a bank cannot be cleared, `/api/demo/reset` with `clear_memory` switches to a fresh bank name.

## Hindsight facts to verify
Do not guess; check https://hindsight.vectorize.io/ and mark unknowns with `# VERIFY IN DOCS`: whether a bank is created on first retain; whether a batch retain exists; whether recall is immediate after retain; retain latency; how the Cloud client takes the API key; the reflect response attribute name; and whether recalled text keeps original wording (the CASE id and `up to 5%` must survive). The smoke test must show this before `memory.py` is built.

## Conventions
Type hints everywhere; pydantic models for all requests and responses; secrets only in `.env`, never committed; mask bank accounts; clean JSON errors, no stack traces; run pytest before calling anything done; no features outside this scope.

## Demo story
The code must support Deccan Freight Lines, `PRICE_MISMATCH`: D1 +4.0% with no memory -> `ESCALATE`; human approves with note "up to 5%". D2 +3.2% and D3 +4.5% -> recall the earlier case(s), recommend approval but confidence below 0.80, so escalate with a recommendation. D4 Godavari IT missing PO Rs. 32,000, no precedent -> `ESCALATE`. D5 +3.8% -> `AUTO_APPROVE_WITH_NOTE` citing the earlier cases. D6 +9% -> `ESCALATE` by G3. D7 bank details changed -> `ESCALATE` by G1.
