# Lead management reference instance (v1)

This is a private, synthetic/local reference workflow inspired by the supplied Upwork brief. It is not a completed GHL delivery.

## Current graph

```text
local webhook payload
  → lead.intake: trim, normalize email/phone, enforce bounded field sizes,
    flag missing/invalid fields, fingerprint payload, set REVIEW_REQUIRED
  → lead.store: PostgreSQL write protected by a unique source event key
```

The normal path deliberately does not call a live LLM, create a CRM contact/opportunity, or send a message. No qualification rubric, GHL test Location, or OpenAI API key is available. The workflow therefore preserves the request and marks it for human review rather than guessing a sales class or summary.

The reusable `crm.ghl_upsert_contact` adapter is now implemented separately and synthetic-test verified. It is not bound to this portfolio workflow because the account, Location, duplicate policy, and field mapping are not configured. See [GHL contact upsert](ghl-contact-upsert.md).

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
- Qualification status is always `REVIEW_REQUIRED` until the owner approves criteria and a model/provider path. No AI claim or score is emitted.
- Intake module responses expose the review status/reasons but suppress name, email, phone, and enquiry text. The database retains the input record and therefore still requires access controls and synthetic-only use for this reference.
- No reply/book/opt-out/manual-pause event source or follow-up suppression scheduler is wired.
- The Google Form draft is unpublished; the local services bind only to loopback. No external endpoint is exposed. The user's VPS is explicitly out of scope.
- This does not establish privacy/legal readiness for real prospect data. Keep current runs synthetic.

## Module boundaries

- `lead.intake`: platform-neutral normalization and review-flag behavior.
- `lead.store`: local PostgreSQL event persistence and event-key idempotency.
- Future GHL, LLM, Google Forms/Sheets, and email implementations should be separate adapters created when an owned test account and actual configuration/job requirements exist.

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
