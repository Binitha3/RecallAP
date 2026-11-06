# RecallAP

## Hindsight notes

Checked against the official Hindsight documentation on 2026-11-06:

- **Client and Cloud authentication:** the Python client is `hindsight-client`; it accepts `Hindsight(base_url=..., api_key=...)`. The Hindsight Cloud Python guide documents `https://api.hindsight.vectorize.io` as the base URL and `HINDSIGHT_API_KEY` as the environment variable.
- **Bank creation:** Hindsight Cloud documentation says banks are created automatically on first use. The API changelog specifically says a first `retain` creates a missing bank. The smoke script intentionally retains into a fresh, unique bank without calling `create_bank()` to confirm behavior with the configured account.
- **Batch retain:** the Python client docs show `client.retain_batch(bank_id=..., items=[...])`. The smoke script checks whether the installed client exposes it and, if so, tries it in a second throwaway bank.
- **Reflect response:** the Python SDK examples read `response.text`; the API reference documents `text` as the primary reflect output. The smoke script also prints the actual response class and public attribute names.
- **Recall timing and retained wording:** Hindsight extracts structured facts from retained content rather than storing the original text verbatim, and its Cloud docs say memory processing can be asynchronous. Whether a recall immediately after retain already returns the new facts, how long retain takes, and whether both `CASE-0001` and the exact phrase `up to 5%` survive in recalled text remain runtime questions. The smoke script prints retain timings, immediate and delayed raw recall text, and exact substring checks.

Run `python scripts/smoke_hindsight.py` after putting your real `HINDSIGHT_BASE_URL` and `HINDSIGHT_API_KEY` in `.env`. It creates uniquely named throwaway banks and leaves them in Hindsight for inspection. Do not proceed to the memory adapter until you have run it and reviewed the output.

### References

- [Hindsight Python client](https://hindsight.vectorize.io/sdks/python)
- [Hindsight retain API](https://hindsight.vectorize.io/developer/api/retain)
- [Hindsight reflect API](https://hindsight.vectorize.io/developer/api/reflect)
- [Hindsight Cloud Python SDK guide](https://docs.hindsight.vectorize.io/cloud/python-sdk/)
- [Hindsight Cloud integration notes](https://docs.dev.hindsight.vectorize.io/cloud/integrations/)
- [Hindsight retain bank creation change](https://hindsight.vectorize.io/blog/2026/04/28/version-0-5-5)
