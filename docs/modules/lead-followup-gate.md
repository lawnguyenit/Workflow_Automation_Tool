# Lead follow-up suppression gate

## Purpose and boundary

`lead.followup_gate` provides a durable, fail-closed eligibility check for a future follow-up send and records lifecycle events that suppress later sends. It never sends, schedules, cancels, or retries a message. A caller must check the gate immediately before each provider send.

The reusable module is registered as `portfolio.lead_followup_gate`. It is not yet connected to the Google Forms polling graph, a GoHighLevel account, an email sender, or any n8n follow-up workflow. No enrollment, schedule, recipient, content, or channel policy is configured.

## Operations

- `check`: accepts `subject_scope` and `subject_id`. Only explicitly enrolled subjects in `ACTIVE` state may proceed. Unknown database state, missing migration/configuration, or any database error blocks the send.
- `record_event`: accepts a stable event identity (`source_system`, `source_account`, `source_event_id`) and one event type:
  - `FOLLOWUP_ENROLLED` permits the gate only when no state exists yet.
  - `REPLIED`, `BOOKED`, and `OPTED_OUT` move the subject to irreversible terminal states.
  - `MANUAL_PAUSE` moves the subject to `PAUSED`.
  - `MANUAL_RESUME` can resume only if the caller identifies the exact currently active pause event. A stale or mismatched resume stays paused and requires review.

The event identity is globally unique across source system/account/event ID. Reuse with a different subject, event type, or payload fingerprint returns a conflict for review. Replaying an identical event does not apply it twice.

## State and concurrency guarantees

- State and event history commit in one PostgreSQL transaction.
- Database connections have a five-second connect timeout; lock waits time out after three seconds and statements after ten seconds. A timeout returns a fail-closed unavailable/review result.
- PostgreSQL transaction-scoped advisory locks serialize event identity and subject transitions across workers. A terminal stop wins over later enrollment or resume events.
- Only HMAC-SHA-256 digests of subject/event identities, enums, outcome codes, and timestamps are persisted. Raw identifiers, contact data, and message content are not stored by this module. `FOLLOWUP_IDENTITY_HMAC_KEY` must be a stable secret of at least 32 bytes and must be stored outside source control.
- Keyed hashes reduce offline guessing from a database-only dump but are not anonymization; repeated identities remain linkable. Protect the key and database and apply a retention policy. Compose passes the key as an API container environment variable, so Docker host/config access must also be restricted.
- No key-rotation or recovery tooling exists. Keep a protected backup of the stable key. If the key is lost/changed, disable follow-up enrollment and sending until an external identity mapping or manual reconciliation has restored all prior stop states; a new key makes old states unreachable and can otherwise allow a fresh enrollment for the same person.
- `DRY_RUN` and `REPLAY` never read state or authorize a send; they return `can_send=false`. `MOCK`/`REAL` use the configured PostgreSQL state store.
- A check is a point-in-time decision. A message already accepted by an external provider cannot be retracted, and a check does not hold a transaction open through the network send. Callers must minimize the check-to-send gap and use provider/outbox safeguards for their own send semantics.
- Suppression applies only to the subject identity and scope chosen by the caller. Account-specific identity mapping, consent basis, retention, and event-source completeness remain caller/business responsibilities.

## Database and operations

Apply `infra/postgres/migrations/005-lead-followup-suppression.sql` through the migration/deployment identity before REAL/MOCK use. Generate `FOLLOWUP_IDENTITY_HMAC_KEY` from cryptographically random bytes (not a human passphrase), store it outside source control, keep a protected recovery copy, and restrict access to the Docker host. The API runtime requires `DATABASE_URL` and `FOLLOWUP_IDENTITY_HMAC_KEY`. The initialized Compose database also creates these tables from `infra/postgres/init.sql` for a fresh local environment. `DRY_RUN`/`REPLAY` remain available without the HMAC key but cannot authorize sending.

Unavailable state fails closed with a safe module error. Persistent provider/event outages need an operator-visible alert in the surrounding workflow; this module does not implement alert delivery, a dead-letter queue, or automated event-source retries.

## Verification status

Unit tests cover strict payload validation, dry-run blocking, hashing/no identifier echo, conflicts/unavailable state, and stale resume review. The local Compose/PostgreSQL integration script covers enrollment, duplicate retry, payload conflict, pause/resume, reply/booking/opt-out terminal behavior, and concurrency. These checks do not exercise a live provider or prove suppression of an already-started send. See [current verification](../project-state/verification.md).
