# GoHighLevel contact tag module (v1)

Status: implemented and synthetic HTTP verified; no live HighLevel account or contact has been used.

## Contract

- Capability/instance: `crm.ghl_contact_tags` / `portfolio.ghl_contact_tags`, bound only when `GHL_LOCATION_ID` is configured.
- Input is exactly `{ "contact_id": "...", "tags": ["..."] }`. The contact must already exist; this module only adds 1–50 unique bounded tag names. It trims surrounding whitespace but does not change case or infer tags from AI qualification.
- Calls HighLevel API v3 `POST /contacts/:contactId/tags` with `contacts.write`. The output suppresses both contact ID and provider tag list.
- Contact upsert continues to reject `tags`; this additive endpoint prevents replacing a contact's full existing tag list.

## Reliability and limits

## Cost and client account

- Synthetic/mock HTTP and PostgreSQL guard checks do not contact HighLevel and do not require a paid account.
- REAL requires the client's active HighLevel Location/token. Account subscription and optional premium workflow/AI/communication features may be billed separately; review the client's billing page before enabling such features. See [HighLevel Pricing & Billing](https://help.gohighlevel.com/support/solutions/articles/155000001156-highlevel-pricing-guide).

- REAL writes require `GHL_PRIVATE_INTEGRATION_TOKEN`, Location configuration, migration `003-external-effect-reconciliation.sql`, and the shared PostgreSQL effect guard. The guard serializes concurrent tag writes for one Location/contact across callers using the same DB.
- A timeout, 5xx, malformed success response, or guard failure after provider work begins becomes an unknown effect held for reconciliation. The operator must inspect the contact's current tag list before clearing the reconciliation row or retrying.
- The module does not claim provider-side idempotency for later repeated successful calls; the API docs describe an add operation but do not state a stable idempotency key. Confirm current tags before operator retries after any ambiguous result.
- `DRY_RUN`, `REPLAY`, and `MOCK` make no provider calls. 429 includes a bounded numeric `Retry-After` metric; automatic sleeping/retry scheduling is not implemented.
- Maximum request: 50 tags, each at most 100 characters, 8 KiB serialized. No bulk write, tag removal, live quota, or concurrent external-writer behavior is claimed.

## Evidence

- Synthetic HTTP tests verify method/path/version/auth/body, whitespace handling, PII-safe output, preview/mock isolation, malformed/auth/rate-limit/unknown responses, and required guard.
- HighLevel's official v3 [Add Tags](https://marketplace.gohighlevel.com/docs/ghl/contacts/add-tags/) endpoint is `POST /contacts/:contactId/tags`, requires `contacts.write`, accepts a string array, and documents HTTP 201. No live request or account credential was used.
