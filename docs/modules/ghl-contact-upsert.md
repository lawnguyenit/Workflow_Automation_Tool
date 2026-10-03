# GoHighLevel contact upsert module (v1)

Status: implemented and verified with synthetic HTTP transport tests; not verified against a live HighLevel account.

## Contract

- Capability: `crm.ghl_upsert_contact`; Python implementation uses HighLevel API v3 `POST /contacts/upsert`.
- Input: `{ "contact": { ...mapped HighLevel contact fields... } }`. The configured `location_id` is added by the module. Contact must include email or phone for HighLevel's configured duplicate matching.
- Output contains only `state`, `contact_id`, `new`, and `side_effect`; it does not echo contact fields or provider response bodies.
- Provider token is read from `GHL_PRIVATE_INTEGRATION_TOKEN` unless injected by the runtime. Do not commit it or put it into workflow exports. `REAL` requires a token; `MOCK`, `DRY_RUN`, and `REPLAY` make no network calls.
- `Version: v3` and a fixed HTTPS provider endpoint are used. No caller-supplied URL is accepted.
- Contact fields are allow-listed. `tags` is rejected because the provider documents that upsert replaces all existing tags; implement tagging as a separate add/remove operation after target tag behavior is agreed.

## Safety and operational behavior

- Duplicate matching is governed by the target Location's “Allow Duplicate Contact” configuration, including its matching priority. An email/phone mismatch can update a different existing contact than expected. Confirm that setting before a live run.
- The module does not provide exactly-once semantics or a cross-system transaction. A read timeout, connection failure, malformed success response, or provider 5xx can occur after the remote write. Those cases return `REVIEW_REQUIRED` / `UNKNOWN`; reconcile the contact before replaying.
- HTTP 401/403 returns a non-retryable credential/scope error. Request validation errors return non-retryable failure. HTTP 429 is surfaced with a numeric `Retry-After` hint when present; the module does not sleep or retry. A caller must enforce a bounded backoff policy and reconcile if its runtime cannot distinguish rejected requests.
- There is no local idempotency ledger for CRM identities and no protection against two independent source events representing the same person. Source-event idempotency remains the responsibility of the intake/store composition.
- Minimum relevant credential scope is `contacts.write` for a private integration. Keep token in n8n credential storage or an equivalent secret manager. Private Integration tokens are fixed until rotated; define rotation and revoke access when no longer needed.
- No data retention is added by this module. Logs/results should retain provider contact ID and sanitized status only, with workflow correlation IDs and access controls.
- Capacity and rate-limit scheduling are caller concerns. HighLevel's current public API docs state 100 requests per 10 seconds and 200,000 per day per app/resource for OAuth requests and provide response rate headers; this module does not coordinate quotas across workers.

## Evidence and remaining gaps

- Synthetic tests verify the v3 URL/version/auth envelope, allow-list, PII suppression, no-network preview modes, 401/403 handling, rate-limit hint, and uncertain remote outcomes.
- No live token, Location ID, field mapping, or duplicate policy is configured. No contact was created or updated. Live end-to-end behavior remains unverified.
- API references: [Upsert Contact](https://marketplace.gohighlevel.com/docs/ghl/contacts/upsert-contact/index.html), [Add Tags](https://marketplace.gohighlevel.com/docs/ghl/contacts/add-tags/), [Private Integrations](https://marketplace.gohighlevel.com/docs/Authorization/PrivateIntegrationsToken/index.html), and [Rate Limits](https://marketplace.gohighlevel.com/docs/other/rate-limits/index.html).
