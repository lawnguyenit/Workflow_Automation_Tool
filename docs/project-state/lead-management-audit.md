# Independent audit — local lead-management reference

## Follow-up audit update — 2026-10-03

The original audit below captured the pre-0.4.0 state and is retained as historical evidence. The independent re-review of 0.4.0 found the full Upwork brief remains incomplete, while confirming the newly composed `lead.intake → lead.qualify/review → lead.store` path is only locally verified with synthetic fixtures in `DRY_RUN`. The Docker API returned `CLASSIFIED` qualification and `PREVIEWED` storage with synthetic name/email/phone omitted from its aggregate response; this was re-run after the fixes below.

- **Resolved:** a null classification now adds the actionable `unclear_fit` review reason; a dedicated unit test covers it.
- **Resolved:** removed the duplicate API test function name so pytest collects the intended test definition unambiguously.
- **Current verification:** 95 tests pass with one Starlette deprecation warning; `compileall`, `git diff --check`, and Docker API rebuild/preview pass.
- **Still incomplete:** connected form/webhook, GHL opportunity/custom fields/tags, email sequence, reply/booking/opt-out/manual-pause suppression, lead-specific alert/recovery, n8n UI import/runtime, live provider checks, and end-to-end acceptance.
- **Material risk:** REAL qualification retries can repeat model cost because no durable call ledger/cache/spend ceiling exists; prompt provenance does not yet bind a rubric version/hash.

## Reviewed scope and authority

- Target: the supplied Upwork brief for form/webhook capture, validation, AI qualification, GHL contact/opportunity updates, follow-up with suppression, failure handling, export/setup documentation, and an end-to-end acceptance run.
- Account constraints: Gmail/Outlook with 2FA; no identified CRM/GHL account, no OpenAI API key, no public HTTPS endpoint; local form is unpublished; the user's VPS hosts a game and was not touched.
- Evidence inspected: `docs/plan.md`, module contract docs, registry/runner/API code, lead modules and schema/migration, n8n export, unit tests, Compose PostgreSQL records, current `verification.md`, and workspace catalog.
- Independent audit was read-only. The lead verified the targeted audit fixes by source inspection; the final 76-test run and Docker integration were run by the implementation lead, not by the auditor.

## Findings

| ID | Severity/state | Finding | Evidence / reproduction | Impact / next action |
|---|---|---|---|---|
| AUD-01 | High / incomplete scope | The implementation is not a completed deliverable for the full Upwork brief. | Current graph is `lead.intake → lead.store`; n8n export is hard-coded synthetic `DRY_RUN`. No connected source, AI, GHL, outbound messages, suppression events, or failure alerts. See `lead-management-reference.md` and `verification.md`. | Continue only when required accounts/business settings exist. Do not advertise this as a completed GHL lead automation. |
| AUD-02 | Resolved / source-inspected | Direct intake no longer masks the human-review state. | Intake returns `PARTIAL`; API exposes qualification status/reasons only and suppresses identity fields. | Final unit suite has a focused API boundary test. |
| AUD-03 | Resolved / source-inspected | Malformed qualification data can no longer fail after persistence. | `lead.store` validates exact record/fingerprint shape, JSON safety, reason count/text and optional qualification fields before opening the database connection. | Unit tests cover malformed qualification, oversized reason lists, and fingerprint mismatch. |
| AUD-04 | Resolved / evidence index refreshed | Shared module catalog previously lacked both lead modules and had stale test totals. | Workspace `docs/MODULE_CATALOG.md` now includes the two entries and 76-test evidence. | Recheck index when implementation changes. |
| AUD-05 | Risk / deliberately bounded | Dedupe is source-event scoped only. | Unique key `(source_system, source_account, source_event_id)`; sequential PostgreSQL test confirms duplicate and changed-payload behavior. | No CRM contact/opportunity or outbound message dedupe. No actual external webhook retry or concurrent race test. |

## Criterion matrix

| Brief criterion | Result | Evidence / limitation |
|---|---|---|
| Live form/webhook capture | Not tested / incomplete | Local API accepts the caller's payload; no connected trigger supplies a source event ID. Private Google Form remains unpublished. |
| Normalize and validate | Pass, bounded | Trims strings, normalizes email/phone, flags missing/invalid fields, rejects oversize/type errors. Phone country/E.164 semantics are not inferred. |
| Duplicate submissions | Pass for repeated stable event key; otherwise not tested | PostgreSQL sequential local test; no identity/CRM/message dedupe and no concurrency run. |
| AI extraction/classification/summary | Not implemented | No approved criteria or API key; fail-closed `REVIEW_REQUIRED`. |
| GHL contacts/custom fields/tags/opportunities | Not implemented | No GHL account/Location or pipeline mapping. |
| Acknowledgement/follow-up and stop events | Not implemented | No sender credential, reply/booking/opt-out/pause connector, or suppression scheduler. |
| Monitoring/retry/alerts/recovery | Partial / not tested end-to-end | Sanitized module error states and DB idempotency exist; no lead side-effect outbox, external alerting, retry scheduler, or recovery exercise. |
| n8n export/setup | Partial pass | Export and static topology verified; n8n UI import/run not done because local UI requests owner sign-in. |
| End-to-end supplied-job success | Fail / not delivered | Synthetic local API-to-PostgreSQL path works; required external system interactions are absent. |
| User acceptance | Decision needed | No acceptance claimed. |

## Audit disposition

- Full Upwork scope remains incomplete by design/account availability; user account and business inputs determine the next binding work.
- Verified behavior: 76 unit tests, compile checks, repeatable migration, local Docker API/PostgreSQL integration for stored/duplicate/conflict/missing-field review, and API output redaction.
- Not verified: n8n UI execution, connected form ingestion, email, GHL, OpenAI, follow-up suppression, concurrent database behavior, or client acceptance.
- No email was sent and no CRM account was modified. Four synthetic rows remain in the local database.
