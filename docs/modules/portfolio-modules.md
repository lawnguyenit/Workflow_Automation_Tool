# Portfolio module guarantees and limits

This document records behavior that operators and clients commonly ask about. “Verified” below means covered by local unit tests with synthetic fixtures/mocks; it does not mean proven against a live account or production database.

## Shared runtime contract

Every result includes `module_id`, contract version, `SUCCESS` / `PARTIAL` / `FAILURE` / `REVIEW_REQUIRED`, business result, safe error codes with retryability, warnings, metrics, workflow/run/correlation IDs, and timestamps. The runner stops after `FAILURE`, `REVIEW_REQUIRED`, or a `PARTIAL` result unless that step explicitly permits continuing. Missing instances, executor failures, and mapping exceptions are normalized without echoing payloads or connection details.

`DRY_RUN` is the default API behavior. It skips database writes and external output. The API does not enforce fixture-only input, so a caller may submit real invoice data even in preview mode. It also does not verify that `REPLAY` input was captured or sanitized; callers must sanitize replay data before submitting it. The workflow response removes email attachment bytes and extracted PDF text before returning data that n8n may retain. The extracted invoice fields still appear in outputs and may be sensitive; apply access control and retention limits in every orchestration platform.

## Input: `email.ingest` (`modules/input/email_ingest.py`)

- **Sources:** synthetic JSON fixture or Gmail REST read. Gmail uses GET operations and does not mark mail read, modify labels, or delete messages. Live Gmail requires a read-only access token and stable mailbox/account scope.
- **OAuth caveat:** the `gmail.readonly` API scope is classified by Google as restricted and allows reading message content/settings. Choose the narrowest usable scope; the module does not implement OAuth consent/verification. Live client jobs involving email or contact data need the workspace's data-policy triage and source/jurisdiction review before delivery.
- **Bounds:** one Gmail list page, at most 50 messages, 10 MiB per attachment, 25 MiB aggregate attachment bytes, 500-character query. No pagination/checkpoint is implemented. The fixture is synthetic.
- **Duplicate behavior:** provider message and attachment IDs are preserved as source identity. The module does not dedupe messages itself; storage applies the cross-run source key.
- **Failure/recovery:** invalid fixture/configuration is non-retryable; provider timeouts and transient HTTP status follow explicit safe result codes. Re-run only after checking mode and source scope. Use fixture ID for reproducible debugging.
- **Data handling:** provider attachments must be fetched for extraction; the workflow API suppresses bytes in its response. Direct module invocation returns attachment bytes to its caller, so that caller must protect execution data.
- **Unverified:** OAuth lifecycle, pagination/checkpointing, Gmail quota behavior, mailbox retention, live API behavior, and high-volume backpressure.

## Processing: `document.extract` (`modules/processing/document_extract.py`)

- **Supported input:** bounded, machine-readable PDF attachments using `pypdf`; preserves source message/attachment IDs and a SHA-256 content digest.
- **Bounds/failure:** default maximum 10 MiB/file, 100 pages and 500,000 extracted characters (portfolio instance lowers the page limit to 50). Unsupported, encrypted, malformed, image-only/scanned, or over-limit files require review. No OCR is claimed.
- **Duplicates/recovery:** repeated source documents remain individually observable; dedupe is a later storage concern. Replaying the same fixture is deterministic.
- **Capacity:** extraction is synchronous and in-memory; use an explicit worker/queue and stricter aggregate limits before accepting sustained production volume.
- **Unverified:** malformed/adversarial PDF resource exhaustion under hostile workloads; actual scanned-document OCR quality.

## Processing: `llm.structured_extract` (`modules/processing/llm_structured_extract.py`)

- **Output:** invoice-shaped fields, source provenance, provider/model, confidence, missing fields, conflicts, and explicit review status. Missing or ambiguous values remain unset.
- **Modes:** mock/dry-run/replay use deterministic local parsing or explicit test fixtures. Real mode sends extracted document text to OpenAI Responses API with strict JSON Schema and requires `OPENAI_API_KEY` plus `OPENAI_MODEL`.
- **Failure/retry:** timeouts, transport, rate limits, and provider server failures are retryable; invalid output/configuration is not. Error results do not include API keys or response bodies.
- **Idempotency/cost:** extraction is read-only but can incur repeated provider cost in REAL mode. There is no provider-call cache or global request budget. `record_key` is a stable document identity aid, not a guarantee that LLM output is identical across models/runs.
- **Unverified:** live model/schema compatibility, quality, token cost, provider retention, data-region settings, and rate limits. The local parser is a fixture helper, not an invoice OCR/extraction substitute.

## Processing: `data.validate` (`modules/processing/validate.py`)

- **Rules:** required fields, finite non-negative amount, currency shape, optional currency allowlist/amount bounds/confidence threshold, and declared output fields. Business-specific tax/accounting rules must be configured by the job owner.
- **Duplicates:** duplicate `record_key` within one batch is rejected after its first occurrence. Cross-run/concurrent dedupe is handled by PostgreSQL storage.
- **Failure:** malformed records return item-level safe codes; mixed accepted/rejected batches are partial. Validation rejection is not retried. Defaults are template choices, not client accounting policy.

## Storage: `storage.record` (`modules/storage/postgres_store.py`)

- **Identity:** unique composite key `(source_system, source_account, source_message_id, source_attachment_id)`; empty attachment ID means one document per message. Accounts must be stable and tenant-scoped.
- **Concurrency/idempotency:** insert uses a unique constraint and `INSERT ... ON CONFLICT DO NOTHING`, then reads the canonical row. Concurrent same-key submissions converge to one stored row. The policy is first-write-wins; a duplicate with changed normalized fields is reported but does not overwrite the existing record.
- **Transaction/recovery:** one input batch writes in one database transaction. Retry a confirmed temporary failure with the same source keys. Unknown commit outcome is marked unknown; replaying those keys is safe. `DRY_RUN`/`REPLAY` do not connect to PostgreSQL.
- **Outbox:** notification intents use an idempotency key and payload fingerprint. A conditional database update claims only `PENDING` intents, so only one concurrent worker can attempt provider delivery. A timeout becomes `UNKNOWN`; delivery is not automatically retried because the provider may already have accepted it. Human reconciliation is required.
- **Capacity/ops:** API requests are synchronous; there is no connection pool, queue, retention job, or migration runner beyond repeatable initial SQL. Use least-privilege runtime DB credentials, backups, monitoring, and a migration process for client deployments.
- **Unverified:** actual PostgreSQL transaction/concurrency behavior on a running server, restore/backup, connection saturation, and schema upgrade from a prior released version.

## Output: `notify.send` (`modules/output/notify.py`)

- **Destinations:** local/mock outbox and Slack incoming webhook. Notification content is deliberately limited to invoice number, amount, currency and record ID; sender and raw document text are omitted.
- **Idempotency/concurrency:** event key derives from destination and canonical persisted record ID. An atomic outbox claim blocks a second worker from sending an already claimed event.
- **Failure/retry:** only Slack's `200 ok` response is marked sent. An HTTP 429 is stored as `FAILED` and is not labeled retryable because there is no delayed requeue/scheduler; operator reconciliation is required. Timeout/transport or a post-delivery DB update failure has an ambiguous outcome; state is unknown and automatic resend is blocked.
- **Security/limits:** only HTTPS `hooks.slack.com` webhook URLs are accepted. No OAuth, arbitrary URL, or dynamic destination is supported. Keep webhook credentials outside source and logs.
- **Unverified:** live Slack behavior, rate limiting, provider-side duplicate semantics, and user acceptance of the notification contents.

## Output: `gmail.send` (`modules/output/gmail_send.py`)

- **Purpose:** Send one plain-text email through Gmail API v1 with a PostgreSQL-backed idempotency outbox.
- **Contract and recovery:** See [`gmail-send.md`](gmail-send.md) for exact input/output, key scope, concurrency behavior, rate-limit handling, unknown-outcome reconciliation, and PII retention.
- **Unverified:** The module's local OAuth token and Gmail API path, n8n credential wiring, sender policy, approved lead email text, and follow-up suppression. A self-test through the connected Gmail app verifies only that separate account connection.

## Execution/deployment: API, Docker Compose and n8n

- Python runner and HTTP API expose catalog, module instance, workflow definition, and workflow instance routes. The n8n template is a manual trigger with six HTTP module-instance nodes and explicit `SUCCESS` gates; n8n composes the modules for this demo. The aggregate workflow endpoint is also available for API-driven execution through the Python runner.
- The API has optional shared bearer-token authentication. `MOCK`/`REAL` and live Gmail reads require `WORKFLOW_API_KEY`; when it is unset, those operations are rejected. `DRY_RUN`/`REPLAY` are allowed without the key to support the n8n demo. The API does not enforce synthetic-only preview payloads or verify that replay inputs are captured and sanitized. This is a single shared key, not per-user authorization or role separation. Keep the API on loopback/private service networking and add deployment-grade identity, TLS, rotation, and access policy before publishing it outside a trusted network.
- Compose separates n8n's database from workflow data in PostgreSQL and binds published ports to loopback. The app runs as non-root. `.env.example` contains placeholders only. The image installs the locked Python environment using uv; the Docker build and local Compose startup have been verified.
- In the current local environment, API and n8n health endpoints return 200 and PostgreSQL is healthy. The synthetic API workflow and sequential database idempotency were verified as recorded in project-state verification. Importing/running the exported n8n workflow remains unverified. Choose and pin an n8n image version when deploying; `stable` is a convenience default, not a reproducible production pin.
- n8n may persist node input/output in execution history. The workflow endpoint omits raw attachment bytes and extracted text; operators still need to set execution retention, access controls, backups, and deletion policies for structured invoice fields.

## Primary references checked (2026-10-03)

- Google classifies `gmail.readonly` as a restricted scope and recommends using the narrowest scope; storage/transmission of restricted-scope data may require a security assessment: [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes).
- OpenAI describes `json_schema` strict Structured Outputs and notes only a subset of JSON Schema is supported: [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).
- PostgreSQL documents `ON CONFLICT DO NOTHING` and the role of unique constraints under concurrent activity: [INSERT reference](https://www.postgresql.org/docs/current/sql-insert.html). This supports the design rationale, but does not replace a live database contention test.
- The official PostgreSQL Docker image documents a version-specific `PGDATA` path and the PostgreSQL 18 volume-path change; Compose mounts the parent data directory accordingly: [official image definition](https://github.com/docker-library/postgres/blob/master/18/trixie/Dockerfile), [official image notes](https://github.com/docker-library/docs/blob/master/postgres/README.md).
- n8n documents execution-history visibility/retry and queue mode for scaling; the checked-in demo disables saved execution data, while production retention and worker topology must be selected for the target installation: [execution history](https://docs.n8n.io/workflows/executions/all-executions/), [queue mode](https://docs.n8n.io/hosting/scaling/queue-mode/).

## Lead-management modules (reference instance v1)

See [lead-management-reference.md](lead-management-reference.md) for the full contract, current graph, guarantees, and limitations.

- `lead.intake` normalizes bounded lead fields, flags missing/invalid values, and fails closed to `REVIEW_REQUIRED` because no owner-approved qualification rubric or live model exists.
- `lead.store` persists one row per source event using a PostgreSQL unique constraint. It is first-write-wins and distinguishes an identical duplicate from the same event key carrying a changed payload.
- `crm.ghl_upsert_contact` is a separate reusable GoHighLevel API v3 adapter. It uses Location duplicate rules, excludes tags to avoid replacing the full tag list, suppresses contact PII in results, and reports ambiguous writes for reconciliation. It has synthetic HTTP tests only; no live account is configured. See `docs/modules/ghl-contact-upsert.md`.
- `crm.ghl_upsert_opportunity` is a separate reusable HighLevel API v3 adapter. It searches open opportunities by Location/contact/pipeline, creates on no match, updates one match, and holds multiple matches for reconciliation. Search-then-write is not atomic under concurrency. It has synthetic HTTP tests only and is not composed into the lead graph without account-specific pipeline IDs. See `docs/modules/ghl-opportunity-upsert.md`.
- `lead.qualify` requires an explicit rubric/labels, emits a strict structured result via the OpenAI Responses API only in `REAL` mode, and otherwise requires a fixture/replay or holds for manual review. It has no durable model-call idempotency ledger/cost ceiling yet; repeated REAL runs can cost again. See `docs/modules/lead-qualification.md`.
- The key is `(source_system, source_account, source_event_id)`. It prevents repeated intake rows for the same upstream event, but does not deduplicate contacts/opportunities in GHL or repeated messages.
- The private Google Form draft remains unpublished, and the n8n local demo is preview-only by default. No CRM, LLM, or email side effect is bound to this graph.
- Capacity, simultaneous contention, external-provider semantics, public ingress, and live form-to-database execution remain unverified. Use only synthetic data until the relevant accounts and data-handling decisions are established.
