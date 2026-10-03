# GoHighLevel opportunity upsert module (v1)

Status: implemented and verified with synthetic HTTP transport tests; not verified against a live HighLevel account.

## Contract

- Capability: `crm.ghl_upsert_opportunity`; Python implementation uses HighLevel Opportunities API v3.
- Input is `{ "opportunity": { "contactId", "pipelineId", "pipelineStageId", "name", "status", ... } }`. The Location ID comes from the module instance configuration. Optional mappings include monetary value, expected close date, forecast probability, assignee, and opportunity custom fields.
- This capability handles open opportunities only; `status` must be `open`. `REAL` first searches open opportunities matching the exact Location, contact, and pipeline. No match creates; exactly one match updates that record's stage and supplied fields; multiple matches stop with `AMBIGUOUS_OPEN_OPPORTUNITY` for reconciliation. Closed opportunities are not selected.
- `DRY_RUN`, `REPLAY`, and `MOCK` do not make network calls. Successful output contains only `state`, `opportunity_id`, `new`, and `side_effect`; provider bodies and business fields are not echoed.
- The API runtime reads `GHL_PRIVATE_INTEGRATION_TOKEN` from its secret environment and binds `portfolio.ghl_opportunity_upsert` only when `GHL_LOCATION_ID` is configured. The token needs both `opportunities.readonly` and `opportunities.write` scopes. A Location ID is configuration, not a secret.

## Failure and reliability behavior

- Input fields are allow-listed and JSON-size bounded to 32 KiB; custom fields are bounded to 100 entries. Invalid mappings are rejected before network access.
- Search is read-only: search timeouts/server errors are safe to retry; a 401/403 is non-retryable; 429 is surfaced as retryable without sleeping.
- `REAL` writes require migration `003-external-effect-reconciliation.sql` and `DATABASE_URL`. A PostgreSQL session advisory lock serializes this adapter's workers by a SHA-256 key derived from Location/contact/pipeline; a busy lock returns retryable review without calling HighLevel.
- Before provider I/O, the guard persists an `IN_PROGRESS` row. Confirmed writes and known outcomes clear the row. Timeouts, connection loss, malformed success responses, and unexpected worker interruptions retain/update the row, so a later run stops for reconciliation instead of blindly issuing another create. The table stores hashes, effect type, correlation ID, and error code, never provider payloads or tokens.
- Search then create/update remains non-atomic at the provider. The guard prevents this adapter's concurrent workers sharing the same PostgreSQL database from racing each other, but it cannot coordinate direct HighLevel edits, another integration, or an old deployment not using this guard. This is not an exactly-once guarantee across all writers. Distinct source events for the same contact intentionally share the same opportunity effect key.
- To resolve a pending row, an operator must first inspect HighLevel for the matching open opportunity and decide whether the intended effect completed. Use the returned hashed `effect_key` to locate the row; use the protected run/source record to identify the Location/contact/pipeline to inspect. After that verification, set `resolved_at` and a bounded, non-sensitive `resolution_ref` on that exact row. Do not auto-delete or retry unknown effects. A later guarded run may then claim the key again.

```sql
UPDATE workflow_external_effect_reconciliations
SET resolved_at = now(),
    resolution_ref = 'verified in HighLevel; incident INC-123'
WHERE effect_key = '<64-character SHA-256 effect key from the run>'
  AND resolved_at IS NULL;
```

The `resolution_ref` must not contain contact details, provider response bodies, or credentials. Keep the CRM inspection and row resolution in the same operator incident record.
- At most 10 pages of 100 opportunities are searched. The module uses the provider's total/next-page metadata and increments the documented page parameter; it does not follow provider-returned URLs. If the safety ceiling is reached, it stops without writing. Provider rate-limit scheduling across workers remains the caller's responsibility.
- There is no CRM account, Location, pipeline/stage mapping, or token configured in the current portfolio. No live opportunity was created or changed. Local Docker PostgreSQL integration used synthetic HTTP transport and verified that concurrent runs made one create, a later retry updated that single record, and an unknown provider result blocked a subsequent request.

## Composition boundary

The contact upsert and opportunity upsert are separate instances. The caller must pass the verified `contact_id` output of `crm.ghl_upsert_contact` as `opportunity.contactId` and supply account-owned pipeline/stage IDs. This module is cataloged and callable, but intentionally not added to the lead workflow graph before the user has a GHL Location and approves its pipeline mapping.

## Evidence and references

- Synthetic tests cover create, update, zero/one/multiple match behavior, pagination, no-network modes, validation, PII-suppressed result shape, auth failures, and retry/unknown outcome boundaries.
- Official API references: [Search Opportunity](https://marketplace.gohighlevel.com/docs/ghl/opportunities/search-opportunity), [Create Opportunity](https://marketplace.gohighlevel.com/docs/ghl/opportunities/create-opportunity/), and [Update Opportunity](https://marketplace.gohighlevel.com/docs/ghl/opportunities/update-opportunity/).
