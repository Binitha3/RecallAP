# RecallAP

## Hindsight notes

Checked against the official Hindsight documentation on 2026-11-06:

- **Client and Cloud authentication:** the Python client is `hindsight-client`; it accepts `Hindsight(base_url=..., api_key=...)`. The Hindsight Cloud Python guide documents `https://api.hindsight.vectorize.io` as the base URL and `HINDSIGHT_API_KEY` as the environment variable.
- **Bank creation:** Hindsight Cloud documentation says banks are created automatically on first use. The API changelog specifically says a first `retain` creates a missing bank. The smoke script intentionally retains into a fresh, unique bank without calling `create_bank()` to confirm behavior with the configured account.
- **Batch retain:** the Python client docs show `client.retain_batch(bank_id=..., items=[...])`. The smoke script checks whether the installed client exposes it and, if so, tries it in a second throwaway bank.
- **Reflect response:** the Python SDK examples read `response.text`; the API reference documents `text` as the primary reflect output. The smoke script also prints the actual response class and public attribute names.
- **Observed Cloud smoke run (2026-11-06):** Retaining the three seeds into a new bank without `create_bank()` succeeded. The three synchronous retain calls took 3.307s, 2.932s, and 3.486s (9.725s total). Immediate recall returned 8 facts; recall after 10 seconds returned 13. The immediate results already included the price rule, so recall was effectively immediate for this run. Batch retain succeeded in a second throwaway bank. Reflect returned a `ReflectResponse` with `text`, `based_on`, `structured_output`, `structured_output_error`, `trace`, and `usage` attributes.
- **Observed text behavior:** The recalled fact text included the exact phrase `up to 5%`, but none of the recalled fact texts included `CASE-0001`. Hindsight extracted/paraphrased the retained case, so parsing CASE IDs from fact `.text` alone is not reliable. The smoke script now probes both fact text and documented source chunks (`include_chunks=True`), and writes `case_id` metadata on the test resolution record. It needs one more run to verify whether source chunks or metadata preserve the ID; `memory.py` must use the verified channel and must not guess.

Run `python scripts/smoke_hindsight.py` after putting your real `HINDSIGHT_BASE_URL` and `HINDSIGHT_API_KEY` in `.env`. It creates uniquely named throwaway banks and leaves them in Hindsight for inspection. Before building the memory adapter, rerun the updated probe once and confirm whether `CASE-0001` is available in source chunks or recall metadata.

### References

- [Hindsight Python client](https://hindsight.vectorize.io/sdks/python)
- [Hindsight retain API](https://hindsight.vectorize.io/developer/api/retain)
- [Hindsight reflect API](https://hindsight.vectorize.io/developer/api/reflect)
- [Hindsight Cloud Python SDK guide](https://docs.hindsight.vectorize.io/cloud/python-sdk/)
- [Hindsight Cloud integration notes](https://docs.dev.hindsight.vectorize.io/cloud/integrations/)
- [Hindsight retain bank creation change](https://hindsight.vectorize.io/blog/2026/04/28/version-0-5-5)
