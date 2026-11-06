# RecallAP — Accounts Payable Exception Agent

RecallAP compares invoices with purchase orders (POs), goods receipt notes (GRNs), and vendor records. Plain Python detects exceptions. Hindsight retrieves relevant vendor knowledge and past decisions. Groq proposes an action, and deterministic policy checks decide whether it can proceed or must go to a human.

This repository contains Member 1's backend and agent work plus Member 3's verified 60-invoice dataset and ON/OFF experiment runner. It does not include the teammate-owned frontend UI or dataset generator. OCR, login, ERP integration, payments, and email sending are out of scope.

## Setup and run

Use Python 3.11. From the repository root in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and add your own keys. Keep `.env` private; it is ignored by Git.

```dotenv
GROQ_API_KEY=your_groq_key
GROQ_MODEL_PRIMARY=openai/gpt-oss-120b
GROQ_MODEL_FALLBACK=qwen/qwen3.8-27b
HINDSIGHT_BASE_URL=https://api.hindsight.vectorize.io
HINDSIGHT_API_KEY=your_hindsight_key
BANK_ID=recallap-demo
RETAIN_AGENT_DECISIONS=false
```

Run the API and open the interactive API page at `http://127.0.0.1:8000/docs`:

```powershell
python -m uvicorn backend.main:app --reload
```

The app now uses Member 3's verified dataset in `data/` (60 invoices, 12 purchase orders, 12 GRNs, and 12 vendors). It falls back to `data/stub/` if those files are removed. Start with `GET /api/invoices` and `GET /api/invoices/INV-2025-7301`. To run the deterministic D1–D7 walkthrough without calling Groq or Hindsight:

```powershell
python scripts\walking_skeleton.py
```

To run the real Hindsight smoke check (it creates uniquely named throwaway banks):

```powershell
python scripts\smoke_hindsight.py
```

To run Member 3's simulated approver checks and full memory comparison, start the API first, then use a second PowerShell window:

```powershell
python scripts\simulate_approver.py
$env:RECALLAP_API_URL = "http://127.0.0.1:8000"
python scripts\run_experiment.py
```

The experiment processes all 60 invoices with memory on and off. It writes the summary to `data/experiment_results.json`, per-invoice decisions to `data/experiment_decisions.csv`, and an audit trail to `data/experiment_debug.jsonl`. The correct touchless rate is correct automatic decisions divided by all invoices; clean invoices count as correct touchless decisions, while human-reviewed invoices do not. Touchless precision is reported separately as correct automatic decisions divided by all automatic decisions. Unsafe decisions are reported separately.

To prepare a separate live-demo bank with the three Appendix A seed memories, run:

```powershell
python scripts\seed_demo_memory.py
```

The script creates a fresh bank, stores its ID and seed case IDs in the ignored local manifest `data/snapshots/demo_bank.json`, and prints the bank ID. Put that value in `.env` as `BANK_ID=...` and restart the API. This keeps demo seeds separate from the experiment banks; the seed case uses `CASE-9001` to avoid collisions with runtime case numbers.

### Last completed full benchmark (2026-11-06)

On the verified 60-invoice set, the completed memory-on run scored **23.33% correct touchless (14/60)**, with **100% precision across 14 automatic decisions**: 4 clean invoices and 10 exception resolutions. It sent 46 invoices to human review and made **0 unsafe touchless decisions**. Memory-off scored **6.67% (4/60)**, all clean invoices, with 56 human reviews and 0 unsafe decisions. The ON/OFF difference was 16.66 percentage points. These are the last completed full-run figures, before the latest deterministic precedent, duplicate-rejection, and quota-retry improvements. A rerun with those changes is pending because the configured Groq key reached its daily token cap. The JSON and CSV in `data/` are the last completed run; refresh them before presenting results for the updated code.

To run the test suite:

```powershell
python -m pytest --basetemp=logs\pytest-tmp
```

## Architecture

```text
Invoice JSON → deterministic detectors → Hindsight recall (optional)
             → Groq proposal → Python guardrails → status / human review
                                             ↘ retain human resolution
```

- `backend/store.py` loads and validates JSON fixtures, masks bank-account numbers, protects state with a lock, allocates per-bank case IDs, and snapshots local decisions.
- `backend/detectors.py` checks price, quantity, PO, GSTIN, tax-rate, currency, duplicate-invoice, and bank-detail exceptions.
- `backend/memory.py` adapts Hindsight retain, recall, search, and reflect calls. External memory failures fail soft where a decision can still be escalated safely.
- `backend/llm.py` sends an evidence-only proposal request to Groq, validates the response with Pydantic, and returns an escalation if every model attempt fails.
- `backend/policy.py` checks citations and precedent applicability after the model. Model output cannot bypass these checks.
- `backend/agent.py` connects the steps and writes each decision to `logs/decisions.jsonl` (one JSON object per line). Snapshot files and logs are ignored by Git.
- `backend/main.py` exposes the FastAPI API and serves `frontend/` at `/` when frontend files are supplied.

## API

- `GET /api/health` reports API liveness plus live Hindsight and Groq connectivity booleans.
- `GET /api/invoices?status=...` lists invoice summaries; `GET /api/invoices/{id}` returns invoice, PO, GRN, vendor, and detected exceptions.
- `POST /api/invoices/{id}/process?memory=on|off&bank=...` runs the agent. `memory=off` skips recall while preserving the rest of the pipeline.
- `POST /api/invoices/{id}/resolve?bank=...` accepts `{ "action": "APPROVE", "note": "...", "approver": "..." }`, records the human outcome, and attempts to retain it.
- `GET /api/memory/search?q=...&bank=...` searches a bank; `GET /api/vendors/{id}/briefing` returns a Hindsight reflect summary.
- `GET /api/metrics?memory=on|off` reads the corresponding experiment series from `data/experiment_results.json`; it returns empty points and a zeroed summary when that file is absent.
- `POST /api/demo/reset?bank=...` accepts `{ "clear_memory": true|false }`. It clears local state for the selected bank. When `clear_memory` is true, it creates a fresh Hindsight bank before mapping the alias to it, so experiment/live runs remain isolated without relying on a bank-delete API.

Decision responses include the invoice ID, exceptions, recalled memories, model proposal, guardrail result, final status, and latency. Final statuses are `AUTO_RESOLVED`, `VENDOR_QUERY`, `REJECTED`, `NEEDS_REVIEW`, and `CLEAN`.

## Groq decision ladder

The model proposes one of `AUTO_APPROVE`, `AUTO_APPROVE_WITH_NOTE`, `REQUEST_VENDOR_INFO`, `REJECT`, or `ESCALATE`. Its system prompt limits it to supplied invoice facts and recalled memories and asks it to cite only recalled case IDs.

1. Use a unanimous action from at least two matching reusable human precedents when available; exact duplicate detections can be rejected by deterministic rule, with bank-change and amount guardrails still applied.
2. Otherwise call the primary model with a forced `propose_resolution` tool call at temperature 0.1.
3. For a temporary HTTP 429, honor Groq's retry hint and retry the primary request once. A daily token cap skips futile retries.
4. Try the fallback model with the same forced tool schema.
5. Try the fallback model without tools, request JSON only, remove any `<think>...</think>` text, extract the first JSON object, and validate it.
6. If there is still no valid proposal, return `ESCALATE` with confidence 0 and `Agent unavailable; routed to human review.`

The configured primary is `openai/gpt-oss-120b`. The prior `qwen/qwen3-32b` fallback was shut down for standard developer-tier requests on July 17, 2026, so the default is its current supported Qwen successor, `qwen/qwen3.8-27b`. The fallback is configurable through `GROQ_MODEL_FALLBACK`. Groq supports forced tool choice in its OpenAI-compatible API, and its current model page lists tool use for Qwen 3.8 27B. [Groq tool-use docs](https://console.groq.com/docs/tool-use/local-tool-calling) · [model deprecations](https://console.groq.com/docs/deprecations) · [Qwen 3.8 27B](https://console.groq.com/docs/model/qwen/qwen3.8-27b)

## Deterministic guardrails

The policy layer checks every proposal in Python:

- Changed bank details always escalate.
- A non-escalation action needs at least two distinct cited, recalled, human-decided, reusable cases for each exception type, matching the same vendor and exception. One-off manual approvals can be retained for audit but are marked manual-only and cannot justify automation. Agent-decided cases alone do not count. An exact detected duplicate can be rejected even when another exception is present; bank-change and amount guardrails still apply.
- The invoice must stay within a precedent's stated percent or rupee range. If no range can be parsed, a percent variance may differ by at most one percentage point from the precedent.
- Invoice totals above Rs. 2,50,000 escalate. Confidence below 0.80 escalates.
- Citations not found in recall are stripped. If several exception types are present and only some have human precedent, the decision escalates.
- An unavailable or invalid model response escalates for human review.

The final decision action is converted into `AUTO_RESOLVED`, `VENDOR_QUERY`, `REJECTED`, or `NEEDS_REVIEW`. No detector, range check, citation check, or guardrail is delegated to the LLM.

## Hindsight memory format and flow

Memory uses three Hindsight contexts: `vendor_profile`, `resolution_case`, and `approver_preference`. Each operation receives an explicit bank ID. A resolution is retained as one self-contained record that includes its `CASE-####` ID, vendor name and ID, exception type, reason, applicability range, and `Decided by: human|agent` marker.

For an invoice exception, RecallAP asks Hindsight three focused questions: for a matching precedent, vendor quirks and payment terms, and approver preferences. Results are deduplicated and limited to six records. Memory-off mode skips those questions but still runs detectors, the model, guardrails, and logging. Human resolutions are retained back to Hindsight so later invoices can use them as precedent. Touchless agent decisions are retained only when `RETAIN_AGENT_DECISIONS=true`.

### How RecallAP uses Hindsight memory

RecallAP sends the vendor, detected exception, and invoice detail to the Hindsight bank. Hindsight returns extracted facts that can be easier to search, but an extracted fact may omit exact wording such as a case number. The adapter therefore reads the `case_id` metadata and requests source chunks; for case memories it uses the original retained chunk as the citation text. If the metadata is missing, it looks for the ID in that source chunk. The policy layer then checks the case ID, vendor, exception, decision-maker marker, and range before allowing the LLM's recommendation.

The bank ID is not hard-coded into the Hindsight client. `BANK_ID` selects the default bank, API routes can accept a bank override where specified, and demo reset rotates to a fresh bank instead of assuming a bank-delete API.

## Hindsight verification notes

Checked against the official Hindsight documentation on 2026-11-06 and confirmed with the smoke script:

- The Python package is `hindsight-client`; the Cloud client accepts `Hindsight(base_url=..., api_key=...)`. The Cloud base URL is `https://api.hindsight.vectorize.io`.
- The app explicitly creates a fresh bank on reset before using it. Batch retain is available in the installed client. The smoke script probes retain, recall, and batch behavior in throwaway banks.
- Reflect returns its answer in `.text`.
- Two real Cloud smoke runs successfully retained three seed memories without an explicit create-bank call. The latest three retains took 3.516s, 3.691s, and 2.818s (10.025s total). Immediate recall returned 9 facts; recall after 10 seconds returned 13. The precedent was available immediately. Batch retain succeeded.
- In both recalls, extracted fact text included `up to 5%` but omitted `CASE-0001`. With `metadata={"case_id":"CASE-0001"}`, the ID appeared in result metadata. With `include_chunks=True`, the source chunk preserved the entire case verbatim and untruncated, including both values.
- `/api/health` checks Hindsight's documented `/health/ready` endpoint and Groq's models-list endpoint, each with a short timeout. [Hindsight API readiness](https://hindsight.vectorize.io/api-reference)

### Hindsight references

- [Python client](https://hindsight.vectorize.io/sdks/python)
- [Retain API](https://hindsight.vectorize.io/developer/api/retain)
- [Reflect API](https://hindsight.vectorize.io/developer/api/reflect)
- [Cloud Python SDK guide](https://docs.hindsight.vectorize.io/cloud/python-sdk/)
- [Cloud API integration](https://docs.hindsight.vectorize.io/cloud/api-integration/)
- [Bank creation change](https://hindsight.vectorize.io/blog/2026/04/28/version-0-5-5)
