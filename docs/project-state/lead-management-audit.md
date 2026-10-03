# Independent audit — local lead-management reference

## Capability update — 0.5.0 in progress

The 0.5.0 change adds a separately callable GHL opportunity adapter based on the official v3 search/create/update endpoints. Its proposed policy is open-match-only by exact Location/contact/pipeline: create on zero matches, update one, and hold multiple matches for review. An independent audit found that concurrent search-then-create calls could race. The implementation added a shared PostgreSQL advisory lock and durable pre-write claim/reconciliation row, then exercised simultaneous calls against local Docker PostgreSQL using synthetic HTTP transport. The race produced one create; a retry updated that record; an unknown provider result blocked another provider call. This does not close the original audit gap: there is still no GHL account, the adapter is not composed into the lead workflow, and no live write or end-to-end acceptance run exists.

The current implementation also still lacks GHL contact tag operations, connected source ingestion, outbound email/follow-up suppression, lead-specific alert/recovery orchestration, a durable model-call ledger/budget, and n8n UI import/runtime evidence. Those requirements remain incomplete.

## Follow-up audit update — 2026-10-03

The original audit below captured the pre-0.4.0 state and is retained as historical evidence. The independent re-review of 0.4.0 found the full Upwork brief remains incomplete, while confirming the newly composed `lead.intake → lead.qualify/review → lead.store` path is only locally verified with synthetic fixtures in `DRY_RUN`. The Docker API returned `CLASSIFIED` qualification and `PREVIEWED` storage with synthetic name/email/phone omitted from its aggregate response; this was re-run after the fixes below.

- **Resolved:** a null classification now adds the actionable `unclear_fit` review reason; a dedicated unit test covers it.
- **Resolved:** removed the duplicate API test function name so pytest collects the intended test definition unambiguously.
- **Current verification:** 95 tests pass with one Starlette deprecation warning; `compileall`, `git diff --check`, and Docker API rebuild/preview pass.
- **Still incomplete:** connected form/webhook, GHL opportunity/custom fields/tags, email sequence, reply/booking/opt-out/manual-pause suppression, lead-specific alert/recovery, n8n UI import/runtime, live provider checks, and end-to-end acceptance.
- **Material risk:** REAL qualification retries can repeat model cost because no durable call ledger/cache/spend ceiling exists; prompt provenance does not yet bind a rubric version/hash.

## Opportunity race remediation — 2026-10-03

- **Finding addressed by implementation lead:** concurrent adapter instances could both observe zero opportunities. Migration 003 now supports a durable `IN_PROGRESS` claim before any provider request, and a PostgreSQL advisory lock serializes the exact Location/contact/pipeline effect key across application workers sharing the database.
- **Verification evidence:** 110 unit tests pass. A synthetic concurrent integration against the local Docker PostgreSQL used mocked HighLevel HTTP responses: exactly one create occurred; a subsequent retry updated the existing synthetic record. A simulated 5xx left a pending reconciliation row and the next call made no provider request.
- **Residual risk:** this remediation has not received a second independent audit after implementation. It only coordinates callers using the same guard/database. Direct HighLevel edits, other integrations, or old app versions remain outside the lock. General load, connection-pool, failover, backup/restore, and live HighLevel behavior remain unverified.

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
| AUD-05 | Risk / deliberately bounded | Lead storage dedupe is source-event scoped; CRM effects have separate matching policy. | Unique key `(source_system, source_account, source_event_id)`; sequential PostgreSQL tests plus same-key GHL adapter race check using synthetic transport. | No outbound message dedupe or cross-system exactly-once guarantee; direct CRM writers are outside the adapter guard. |

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
- Not verified: n8n UI execution, connected form ingestion, email, live GHL, OpenAI, follow-up suppression, general database capacity/failover, or client acceptance. Same-key guarded GHL concurrency was checked separately with synthetic provider responses.
- At the time of this original audit disposition, no email had yet been sent and no CRM account had been modified. Four synthetic lead rows remained in the local database; the later app-level self-test is recorded above and did not change CRM state.

## Independent re-review — 2026-10-03 (0.6.0 worktree)

The independent deliverable auditor reviewed the current 0.6.0 implementation and evidence. Its conclusion remains **incomplete for the supplied Upwork brief**: the local lead path has no connected intake, live qualification provider/rubric, GHL account/pipeline binding, composed follow-up/suppression, alert/recovery process, n8n UI run, or end-to-end acceptance. The new Gmail sender is a separately callable output adapter and is not composed into the lead workflow. One app-level self-addressed email test is account evidence only; it does not validate this module or any lead workflow.

- **Gmail module evidence:** local unit suite and a Docker/PostgreSQL harness with an unconditionally mocked Gmail HTTP transport verify outbox claims, duplicate suppression, changed-payload rejection, confirmed-send payload redaction, and unknown-outcome hold. No local Gmail OAuth token or n8n credential was exercised.
- **Audit findings addressed:** project-state wording now distinguishes that single self-addressed app test from the absence of lead-workflow email and CRM writes. The auditor's retention/reconciliation concern remains documented: uncertain sends retain message content until an operator checks Gmail Sent and reconciles the outbox; no automatic retry is safe.
- **Current scope disposition:** do not present this as delivery of the Upwork job. It is a synthetic local reference plus reusable adapters. GHL/OpenAI/live form, policy choices, stop events, and acceptance remain open.
- **Historical note:** the final bullet in the original audit disposition above reflects evidence at that audit date and is superseded only regarding the self-addressed app test; its statement that no CRM account was modified and that four synthetic lead rows remained is unchanged.

## Evidence update — 2026-10-03 (implementation lead; not an independent re-audit)

The selected Forms API poller was imported into the user's local n8n 2.41.6 Personal project using the supported CLI. The saved workflow was exported back from n8n and inspected: it has the expected nine nodes, is inactive, has no assigned credentials, and does not retain manual/success/error execution payloads. The n8n UI showed the poller graph. This supersedes the older audit matrix's statement that the workflow had not been imported, but does not change its broader finding: no Google OAuth/API call or imported-poller run occurred, and the full Upwork brief is still incomplete. This is an implementation-lead evidence update, not an independent re-review.
