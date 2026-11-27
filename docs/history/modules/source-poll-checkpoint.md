# Source polling checkpoint (v1)

`state.source_poll_checkpoint` is a reusable cross-cutting state module for a polling source that needs one durable cursor per opaque source key. The PostgreSQL implementation is bound as `portfolio.google_forms_poll_checkpoint` and used by the Google Forms API poller.

## Contract

Every operation includes `checkpoint_key` and `lease_owner_id`. The key is an opaque, stable source identifier (for example, a Google Form ID), not a person/contact identifier. PostgreSQL stores only its SHA-256 digest. The digest is stable pseudonymous data, not anonymization; the underlying identifier must still be protected at the caller.

| Operation | Additional input | Result states | Effect |
|---|---|---|---|
| `acquire` | none | `ACQUIRED`, `BUSY` | Creates a checkpoint if absent; otherwise returns its cursor and claims an expired/free lease. The same owner can repeat acquire after a lost HTTP response and receive the same active lease token. |
| `renew` | `lease_token` | `RENEWED`, `LEASE_LOST` | Extends only the matching, still-unexpired lease. A lease that expired cannot be revived. |
| `commit` | `lease_token`, UTC RFC3339 `cursor_at` | `COMMITTED`, `LEASE_LOST` | Requires the current unexpired owner/token, stores the greater of current and requested cursors, and releases the lease. Retrying the most recent commit with the same token is idempotent. |
| `release` | `lease_token` | `RELEASED`, `LEASE_LOST` | Releases only the matching lease. Retrying the most recent release with the same token is idempotent. |

`DRY_RUN` and `REPLAY` validate the request but return `PREVIEWED` without touching PostgreSQL. `MOCK` and `REAL` use PostgreSQL and require the API bearer key when one is configured. The per-instance lease duration defaults to 900 seconds and is bounded to 60–7200 seconds.

## Operational behavior

- A database row lock serializes concurrent claims for the same key. One run gets `ACQUIRED`; another gets `BUSY` while that lease is live.
- Lease owner and random UUID token fence checkpoint writes. An expired or replaced lease cannot commit a cursor, even if its old workflow execution later resumes.
- Source cursor meaning and ordering belong to the caller. This implementation accepts UTC timestamps and commits monotonically; adapters for opaque page tokens need a separate cursor contract.
- The database stores no response body, contact data, or raw Form ID. It stores a source-key digest, timestamp, lease metadata, and the most recent completed operation for retry idempotency.
- If a run crashes, its lease becomes available after expiry. If downstream work finishes after the lease expires, checkpoint commit fails closed. The caller must keep downstream effects idempotent because lease fencing cannot cancel a provider request already in flight.
- Database connection, row-lock, and statement waits are bounded. Provider HTTP retries and rate limits remain owned by the poller.

## Google Forms composition

The n8n template acquires before reading the Form, uses the saved timestamp as the inclusive Forms API filter, renews the lease after page retrieval, releases it after an empty poll, and commits only after all response events reach accepted storage states. The n8n execution carries the short-lived lease token; execution payload retention is disabled. If the lease is lost, the workflow stops before storage or before cursor commit, and the next poll replays from the prior cursor. The stable `formId:responseId` key in `lead.store` deduplicates those replays.

This does not make GHL/email/model side effects exactly-once. The current lead workflow ends at PostgreSQL; any later composition must preserve its own durable idempotency/reconciliation gates before adding remote writes. Large backlogs and runs exceeding the lease duration remain unverified; the n8n template is a low-volume reference.

## Verification

- Unit checks cover acquire, `BUSY`, renew, commit, release, stale lease rejection, dry-run no-write, UTC cursor validation, and source-key hashing.
- Synthetic Compose/PostgreSQL integration covers duplicate acquire retry, overlapping owner rejection, renew, monotonic cursor commit, idempotent commit/release retry, expiry/reclaim, stale token rejection, simultaneous claims, and cleanup.
- No Google API call or n8n poller execution is implied by these database checks.
