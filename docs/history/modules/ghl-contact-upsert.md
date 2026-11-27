# GoHighLevel contact upsert module (v2)

Status: implemented and verified with synthetic HTTP transport tests; not verified against a live HighLevel account.

## Contract

- Capability: `crm.ghl_upsert_contact`; Python implementation uses HighLevel API v3 `POST /contacts/upsert` with `contacts.write`.
- Input: `{ "idempotency_key": "<stable source event identity>", "contact": { ...mapped HighLevel contact fields... } }`. The configured `location_id` is added by the module. Contact must include email or phone for HighLevel's configured duplicate matching. Do not put an email/phone or other PII in the idempotency key.
- Output contains only `state`, `contact_id`, `new`, `side_effect`, and a non-sensitive digest key/count metric; it does not echo contact fields or provider response bodies.
- For this module-backed API implementation, the provider token is read from `GHL_PRIVATE_INTEGRATION_TOKEN` in the API runtime environment. Do not commit it or put it into workflow exports/n8n HTTP node data. `REAL` requires a token; `MOCK`, `DRY_RUN`, and `REPLAY` make no network calls. If a workflow calls GHL directly with n8n's native node, use n8n's credential store instead.
- When `GHL_LOCATION_ID` is set at API startup, the runtime binds `portfolio.ghl_contact_upsert` so n8n can call `/v1/instances/portfolio.ghl_contact_upsert/runs`. Without a Location ID, no live-configured instance is listed. The Location ID is account configuration, not a secret; the token remains in the API runtime secret environment.
- `Version: v3` and a fixed HTTPS provider endpoint are used. No caller-supplied URL is accepted.
- Contact fields are allow-listed. `tags` is rejected because upsert can replace the full tag list. A separate additive-only `crm.ghl_contact_tags` module now calls the documented Add Tags endpoint; it does not infer tags or remove existing ones.

## Safety and operational behavior

## Cost and client account

- Synthetic/mock HTTP checks make no HighLevel request and need no paid HighLevel account. A live job requires the client's active HighLevel account/Location and a private integration token with approved scopes.
- HighLevel subscription, premium workflow actions, AI, communication, and other optional products can have separate plan- or usage-based charges. This contact endpoint's mock test does not determine the client's price; check its in-app billing/pricing page and contract before enabling paid features. See [HighLevel Pricing & Billing](https://help.gohighlevel.com/support/solutions/articles/155000001156-highlevel-pricing-guide).

- Duplicate matching is governed by the target Location's “Allow Duplicate Contact” configuration, including its matching priority. An email/phone mismatch can update a different existing contact than expected. Confirm that setting before a live run.
- REAL writes require migration `008-ghl-contact-upsert-ledger.sql`, `DATABASE_URL`, and a stable source-event `idempotency_key` in `source:account:event` form. The key accepts only an opaque bounded character set and is hashed before storage; callers must not put PII in it. A PostgreSQL advisory lock serializes calls using the same Location/event key across callers sharing the database. Before provider I/O, the ledger persists a digest-only `IN_PROGRESS` claim.
- Confirmed success is cached with only the provider contact ID and `new` flag. A same-key/same-payload replay returns the cached outcome with `side_effect: false` and makes no request. A same-key changed payload returns `CONTACT_EVENT_PAYLOAD_CONFLICT`. A timeout, connection failure, malformed success response, provider 5xx/unexpected response, or worker interruption leaves `UNKNOWN`/`IN_PROGRESS`; same-event replays are held until an operator reconciles the exact key. A 429 is surfaced as retryable and removes the claim because no write was accepted.
- This is not exactly-once semantics or a cross-system transaction. The ledger covers one source event and callers sharing its database; it does not deduplicate two distinct source events representing the same person or coordinate other HighLevel integrations. Those behaviors depend on the Location's duplicate policy and must be verified for the client. Apply a retention period that exceeds the source replay window; deleting a successful event row permits that key to write again.
- `createNewIfDuplicateAllowed=true` is rejected because it can deliberately bypass duplicate matching. It is omitted or must be false.
- HTTP 401/403 returns a non-retryable credential/scope error. Request validation errors return non-retryable failure. HTTP 429 is surfaced with a numeric `Retry-After` hint when present; the module does not sleep or retry. A caller must enforce a bounded backoff policy and reconcile if its runtime cannot distinguish rejected requests.
- Source event identity must be stable across n8n retries and unique across source/account namespaces. The input fingerprint is a one-way digest of mapped contact fields; the ledger stores no raw key or contact fields, but database access remains sensitive and restricted.
- Minimum relevant credential scope is `contacts.write` for a private integration. Keep token in n8n credential storage or an equivalent secret manager. Private Integration tokens are fixed until rotated; define rotation and revoke access when no longer needed.
- No data retention is added by this module. Logs/results should retain provider contact ID and sanitized status only, with workflow correlation IDs and access controls.
- Capacity and rate-limit scheduling are caller concerns. HighLevel's current public API docs state 100 requests per 10 seconds and 200,000 per day per app/resource for OAuth requests and provide response rate headers; this module does not coordinate quotas across workers.

## Evidence and remaining gaps

- Synthetic tests verify the v3 URL/version/auth envelope, allow-list, PII suppression, no-network preview modes, source-key requirement, success cache, changed-input conflict, timeout replay hold, 401/403 handling, rate-limit hint, and uncertain remote outcomes. A dedicated Compose/PostgreSQL integration verifies same-event concurrency, confirmed-success cache, and unknown-outcome hold through mocked HighLevel HTTP; no HighLevel account is used.
- Registry tests verify the module instance is omitted without a Location ID and becomes callable in preview mode when configured; no token value is placed in instance metadata.
- No live token, Location ID, field mapping, or duplicate policy is configured. No contact was created or updated. Live end-to-end behavior remains unverified.
- API references: [Upsert Contact](https://marketplace.gohighlevel.com/docs/ghl/contacts/upsert-contact/index.html), [Add Tags](https://marketplace.gohighlevel.com/docs/ghl/contacts/add-tags/), [Private Integrations](https://marketplace.gohighlevel.com/docs/Authorization/PrivateIntegrationsToken/index.html), and [Rate Limits](https://marketplace.gohighlevel.com/docs/other/rate-limits/index.html).
