# Processing reference modules

## `processing.llm_structured_extract`

Extracts invoice-shaped fields from `payload["documents"]`. Each document should include `text`, a `source_id` or `source_ids`, and optionally `filename`. The result is `business_result["records"]`; each record includes a stable key, source IDs, invoice fields, provider/model provenance, confidence, missing fields, conflicts, and an explicit `EXTRACTED` or `REVIEW_REQUIRED` status.

### Execution and failure behavior

- `MOCK` and `DRY_RUN` use an optional document `mock_extraction` fixture or the deterministic local text parser. `REPLAY` uses `replay_extraction` or that parser. These modes never call the provider.
- `REAL` requires `OPENAI_API_KEY` and `OPENAI_MODEL` (or constructor configuration) and sends text to the OpenAI Responses API using strict JSON Schema output. The key and provider response body are never copied to module results or error messages.
- Provider throttling, server errors, timeouts, and transport failures are retryable. Invalid structured output and rejected requests are not. Missing configuration is reported as `PROVIDER_NOT_CONFIGURED`.
- Missing/ambiguous facts remain null. A conflict clears that field and is recorded for review. The local fixture parser is intentionally narrow and is not OCR, a production invoice parser, or a substitute for provider evaluation.
- `record_key` is a SHA-256-derived key based on source IDs, or on filename and text when IDs are absent. It is a stable batch key, not a global deduplication guarantee.

## `processing.validate`

Validates extracted records, returning accepted `records` and item-level `rejected` entries with stable error codes. The default schema requires all five invoice fields and accepts finite non-negative amounts with a three-letter currency code. Configure `required_fields`, `allowed_currencies`, `minimum_amount`, `maximum_amount`, and `minimum_confidence` for a workflow's business rules. Confidence thresholds and currency allowlists are opt-in. Duplicate `record_key` values within a batch are rejected after the first occurrence; this is not cross-run idempotency.

Validation errors are not retried. When both accepted and rejected items exist, the module returns `PARTIAL`; all-rejected batches return `REVIEW_REQUIRED`. This module does not write to a database, establish accounting/tax policy, or prove that a syntactically valid currency is supported by a business.

## Verification boundary

Unit tests use synthetic documents, deterministic fixtures, and `httpx.MockTransport`. They make no live provider calls and contain no client data or credentials. Live API/model compatibility, prompt quality, cost, privacy/retention settings, rate limits, and production behavior remain unverified until configured and assessed in the target environment.
