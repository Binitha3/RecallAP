# RecallAP

## Hindsight notes

Checked against the official Hindsight documentation on 2026-11-06:

- **Client and Cloud authentication:** the Python client is `hindsight-client`; it accepts `Hindsight(base_url=..., api_key=...)`. The Hindsight Cloud Python guide documents `https://api.hindsight.vectorize.io` as the base URL and `HINDSIGHT_API_KEY` as the environment variable.
- **Bank creation:** Hindsight Cloud documentation says banks are created automatically on first use. The API changelog specifically says a first `retain` creates a missing bank. The smoke script intentionally retains into a fresh, unique bank without calling `create_bank()` to confirm behavior with the configured account.
- **Batch retain:** the Python client docs show `client.retain_batch(bank_id=..., items=[...])`. The smoke script checks whether the installed client exposes it and, if so, tries it in a second throwaway bank.
- **Reflect response:** the Python SDK examples read `response.text`; the API reference documents `text` as the primary reflect output. The smoke script also prints the actual response class and public attribute names.
- **Observed Cloud smoke runs (2026-11-06):** Retaining the three seeds into a new bank without `create_bank()` succeeded in both runs. The latest three retains took 3.516s, 3.691s, and 2.818s (10.025s total). Immediate recall returned 9 facts; recall after 10 seconds returned 13. The price precedent was available immediately. Batch retain succeeded in a separate throwaway bank. Reflect returned a `ReflectResponse` with `text`, `based_on`, `structured_output`, `structured_output_error`, `trace`, and `usage` attributes.
- **Verified case-ID channel:** Hindsight's extracted fact text includes the exact phrase `up to 5%`, but omits `CASE-0001`. With `metadata={"case_id": "CASE-0001"}` on retain, recall results include that metadata. With `include_chunks=True`, the associated source chunk preserves the complete retained case, including both `CASE-0001` and `up to 5%`, verbatim and untruncated on immediate and delayed recall. The memory adapter should take the ID from metadata (with a source-chunk regex fallback) and use the source chunk as the case text; it should not parse citations from extracted fact text alone.

Run `python scripts/smoke_hindsight.py` after putting your real `HINDSIGHT_BASE_URL` and `HINDSIGHT_API_KEY` in `.env`. It creates uniquely named throwaway banks and leaves them in Hindsight for inspection.

### References

- [Hindsight Python client](https://hindsight.vectorize.io/sdks/python)
- [Hindsight retain API](https://hindsight.vectorize.io/developer/api/retain)
- [Hindsight reflect API](https://hindsight.vectorize.io/developer/api/reflect)
- [Hindsight Cloud Python SDK guide](https://docs.hindsight.vectorize.io/cloud/python-sdk/)
- [Hindsight Cloud integration notes](https://docs.dev.hindsight.vectorize.io/cloud/integrations/)
- [Hindsight retain bank creation change](https://hindsight.vectorize.io/blog/2026/04/28/version-0-5-5)
