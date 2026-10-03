# Lead management reference instance (v3)

This is a private, synthetic/local reference workflow inspired by the supplied Upwork brief. It is not a completed GHL delivery.

## Current graph

```text
Google Forms API polling (read-only schema + response scopes; inactive template)
  → source.google_forms_api_response: map one response by exact question title/ID and responseId
  → lead.intake: trim, normalize email/phone, enforce bounded field sizes,
    flag missing/invalid fields and fingerprint payload
  → lead.qualify: require an explicit rubric and accepted classes; use a model
    only in REAL mode, otherwise require a synthetic/replay result or hold review
  → lead.store: PostgreSQL write protected by a unique source event key
```

The current portfolio path does not call a live LLM, create a CRM contact/opportunity, or send a message. No qualification rubric, GHL test Location, or OpenAI API key is available. It therefore preserves the request and marks it for human review rather than guessing a sales class or summary. A synthetic rubric/fixture can exercise the classification composition without a paid provider call.

The existing manual/local instance still accepts a canonical lead object. The earlier `portfolio.lead_management_google_form` instance accepts a row from the linked response Sheet. The selected portfolio composition is `portfolio.lead_management_google_forms_api`: it maps one Forms API response using the matching form schema before reusing the same downstream pipeline. Its mapper and inactive polling export are synthetic-tested; owner OAuth, exact live schema, n8n import, and API access remain unverified. See [Google Forms API response adapter](google-forms-api-response.md).

The reusable `crm.ghl_upsert_contact`, `crm.ghl_contact_tags`, and `crm.ghl_upsert_opportunity` adapters are implemented separately and synthetic-test verified. They are not composed into this portfolio workflow because the GHL account, Location, contact duplicate policy, approved tag names, pipeline/stage IDs, and field mapping are not configured. See [GHL contact upsert](ghl-contact-upsert.md), [GHL contact tags](ghl-contact-tags.md), and [GHL opportunity upsert](ghl-opportunity-upsert.md).

## Guarantees and failure behavior

- A source event is keyed by `(source_system, source_account, source_event_id)` with a PostgreSQL unique constraint. Concurrent retries contend on the database constraint, so only one row is created for that exact source event key.
- First write wins. A repeated key with the same fingerprint reports `DUPLICATE`; a repeated key with a different fingerprint reports `PAYLOAD_CONFLICT_REVIEW` and does not overwrite the first payload.
- Before opening a database connection, the store requires the exact v1 record/qualification shape, checks the fingerprint against normalized source fields, verifies JSON-safe values, and bounds review reasons and optional classification/summary/model metadata.
- Email is stripped and lowercased. Phone punctuation/spacing is removed; no country code is guessed, and this is not full E.164 validation.
- Required fields are flagged as missing, not fabricated. Invalid email and missing fields are retained for manual review if a stable source event key exists.
- Input fields have hard maximum lengths. Payload values are stored in JSONB; protect the database and logs as potentially sensitive even in future real deployments.
- `DRY_RUN`/`REPLAY` return `PREVIEWED` without database writes. `MOCK` and `REAL` both exercise the configured PostgreSQL store; use synthetic fixtures only for this portfolio reference.
- Persistence errors return a sanitized stable code and `UNKNOWN` retryability. Retrying the same event is safe for row creation, but callers must preserve the source event ID.

## Explicit limits

- This deduplicates source events only. It does not merge people by email/phone, deduplicate CRM contacts/opportunities, prevent repeated outbound messages, or coordinate a CRM's own duplicate settings.
- There is no transactional outbox for CRM/email side effects in this lead graph. Do not add remote writes to the same run and assume a database transaction makes them atomic.
- Qualification remains `REVIEW_REQUIRED` when approved criteria, required fields, a valid model/fixture result, or provider configuration is absent. Fixture-backed test runs can emit a synthetic classification; no live model claim is made for this instance.
- `lead.qualify` accepts a per-run rubric and output labels, validates classifications/confidence/missing data, uses strict structured output for the OpenAI Responses API, and fails closed to review. It sends service/description free text but excludes separately mapped name/email/phone.
- REAL qualification uses the durable ledger in migration `004-lead-qualification-ledger.sql`, serializes a stable source event across workers, caches a result by input/rubric/model/prompt fingerprint, and requires an explicit UTC-day call-count cap. Unknown attempts and changed event input are held for manual reconciliation. This limits duplicate requests and request count, not USD spend; provider token usage/prices are not used to enforce a money budget. See [lead qualification](lead-qualification.md).
- Intake module responses expose the review status/reasons but suppress name, email, phone, and enquiry text. The database retains the input record and therefore still requires access controls and synthetic-only use for this reference.
- No reply/book/opt-out/manual-pause event source or follow-up suppression scheduler is wired.
- The Google Form is unpublished; the Forms API export is inactive, has a placeholder Form ID and no credential, and defaults to `DRY_RUN`. Its question-title mapping has not been checked against the live Forms API schema. The prior Sheets export remains optional. The local services bind only to loopback. No external endpoint is exposed. The user's VPS is explicitly out of scope.
- The polling cursor is n8n workflow static data. It is a single-instance, low-volume reference only: overlapping runs are not serialized and the cursor is not a distributed checkpoint. Partial batches keep the prior cursor and revisit earlier response IDs; PostgreSQL suppresses same-event replays and flags changed payloads for review. Production multi-worker/high-volume use needs an atomic database checkpoint or lease first.
- This does not establish privacy/legal readiness for real prospect data. Keep current runs synthetic.

## Module boundaries

- `source.google_form_response`: deterministic adapter from one n8n Google Sheets Trigger row to the source-event lead contract; no Google OAuth is held by the API.
- `source.google_forms_api_response`: deterministic mapper from one Forms API `FormResponse` and matching schema to the source-event lead contract; OAuth stays in n8n and the stable event key is `<formId>:<responseId>`.
- `lead.intake`: platform-neutral normalization and review-flag behavior.
- `lead.store`: local PostgreSQL event persistence and event-key idempotency.
- `lead.qualify` is implemented as a configurable LLM/review module; its OpenAI path remains unverified without a separately provisioned API credential. Reusable Gmail send and GHL opportunity modules now exist but are not composed because sender OAuth, GHL, and owner-approved follow-up/suppression policies are unavailable. The Forms API source mapper/poller is implemented as an inactive export; it still needs owner-approved read-only OAuth, live schema/title confirmation, n8n import, and a live event check.

## Local manual run

Use the n8n export at `workflows/n8n/lead-management-local.demo.json` for a preview request. For a PostgreSQL write, submit a synthetic lead to `POST /v1/workflow-instances/portfolio.lead_management_local/runs` with context mode `MOCK` and the local API bearer key set in the user's environment. Never place that key in the workflow export or repository.

Example body (synthetic):

```json
{
  "payload": {
    "lead": {
      "source_system": "manual_test",
      "source_account": "local_portfolio",
      "source_event_id": "synthetic-lead-001",
      "name": "Taylor Example",
      "email": "taylor@example.invalid",
      "phone": "+15550102030",
      "service_requested": "Workflow automation",
      "description": "Synthetic lead for local integration testing."
    }
  },
  "context": { "workflow_id": "lead_management_intake", "attempt": 1, "mode": "MOCK" }
}
```

Send the same event twice to verify the first is stored and the second is reported as a duplicate. Change the body while retaining the source event key to verify conflict review and first-write-wins.
