# GoHighLevel opportunity upsert module (v2)

Status: implemented and verified with synthetic HTTP transport tests; not verified against a live HighLevel account.

## Contract

- Capability: `crm.ghl_upsert_opportunity`; Python implementation uses HighLevel Opportunities API v3.
- Input is `{ "idempotency_key": "source:account:event", "opportunity": { "contactId", "pipelineId", "pipelineStageId", "name", "status", ... } }`. The Location ID comes from the module instance configuration. Optional mappings include monetary value, expected close date, forecast probability, assignee, and opportunity custom fields. Use an opaque stable event key with no PII; preserve it across webhook retries.
- This capability handles open opportunities only; `status` must be `open`. `REAL` first searches open opportunities matching the exact Location, contact, and pipeline. No match creates; exactly one match updates that record's stage and supplied fields; multiple matches stop with `AMBIGUOUS_OPEN_OPPORTUNITY` for reconciliation. Closed opportunities are not selected.
- `DRY_RUN`, `REPLAY`, and `MOCK` do not make network calls. Successful output contains only `state`, `opportunity_id`, `new`, and `side_effect`; a confirmed replay also reports `cached: true`. Metrics expose only hashed `event_key`/`effect_key` values, not provider bodies or business fields.
- The API runtime reads `GHL_PRIVATE_INTEGRATION_TOKEN` from its secret environment and binds `portfolio.ghl_opportunity_upsert` only when `GHL_LOCATION_ID` is configured. The token needs both `opportunities.readonly` and `opportunities.write` scopes. A Location ID is configuration, not a secret.

## Failure and reliability behavior

## Cost and client account

- Synthetic HTTP/PostgreSQL checks do not call HighLevel or require a paid account.
- REAL requires the client's active HighLevel Location/token. Subscription and optional premium workflow/AI/communication products may add plan- or usage-based charges; check the client's billing settings before enabling those products. See [HighLevel Pricing & Billing](https://help.gohighlevel.com/support/solutions/articles/155000001156-highlevel-pricing-guide).

- Input fields are allow-listed and JSON-size bounded to 32 KiB; custom fields are bounded to 100 entries. Invalid mappings are rejected before network access.
- Search is read-only: search timeouts/server errors are safe to retry; a 401/403 is non-retryable; 429 is surfaced as retryable without sleeping.
- `REAL` writes require migrations `003-external-effect-reconciliation.sql` and `009-ghl-opportunity-upsert-ledger.sql`, plus `DATABASE_URL`. A PostgreSQL event ledger caches confirmed success for the source event and rejects changed input. A separate session advisory lock serializes this adapter's workers by a SHA-256 key derived from Location/contact/pipeline; a busy lock returns retryable review without calling HighLevel.
- Before provider I/O, the event ledger and entity effect guard persist separate `IN_PROGRESS` rows. Confirmed success is cached as only `opportunity_id` and `new`; a same-event/same-input replay returns the cached result with `side_effect: false` and makes no HighLevel request. Changed input under the same event key is held. Timeouts, connection loss, malformed success responses, and unexpected worker interruptions leave a reconciliation hold instead of blindly issuing another create. Persisted rows contain event/input/effect digests, status, bounded error code, optional opportunity ID, and boolean; they do not contain provider payloads or tokens.
- Search then create/update remains non-atomic at the provider. The event cache prevents same-event replays from repeating a confirmed write; the entity guard prevents this adapter's concurrent workers sharing the same PostgreSQL database from racing each other. Neither coordinates direct HighLevel edits, another integration, or an old deployment not using these guards. This is not exactly-once delivery across all writers. Distinct source events for the same contact intentionally share the same open-opportunity effect key and still depend on tenant search visibility and duplicate policy.
- For an unknown outcome or a worker-interrupted `IN_PROGRESS`, inspect HighLevel using the protected source/run record and both returned digests: `event_key` identifies `workflow_ghl_opportunity_upserts`; `effect_key` identifies `workflow_external_effect_reconciliations`. If the intended write is verified, transactionally store its verified `opportunity_id` and `new` value as `SUCCEEDED` for the event and resolve the matching effect row. If no write is verified, resolve the effect row and remove only that exact event's `UNKNOWN`/`IN_PROGRESS` row so a retry can run. If the state remains ambiguous, leave both holds in place. Never use an automated retry or delete rows without first verifying the provider state.

```sql
UPDATE workflow_external_effect_reconciliations
SET resolved_at = now(),
    resolution_ref = 'verified in HighLevel; incident INC-123'
WHERE effect_key = '<64-character SHA-256 effect key from the run>'
  AND resolved_at IS NULL;
```

For a verified successful write, apply the cache update and effect-row resolution in one checked transaction. Substitute only the digests from the run result and the verified opportunity result. The block raises and rolls back unless it updates exactly one row in each table:

```sql
DO $reconcile$
DECLARE event_rows INTEGER; effect_rows INTEGER;
BEGIN
    UPDATE workflow_ghl_opportunity_upserts
    SET state = 'SUCCEEDED',
        result = jsonb_build_object('opportunity_id', '<verified-id>', 'new', '<true-or-false>'::boolean),
        error_code = NULL,
        updated_at = now()
    WHERE event_key = '<64-character event digest>' AND state IN ('UNKNOWN', 'IN_PROGRESS');
    GET DIAGNOSTICS event_rows = ROW_COUNT;
    IF event_rows <> 1 THEN RAISE EXCEPTION 'expected exactly one pending event row, updated %', event_rows; END IF;

    UPDATE workflow_external_effect_reconciliations
    SET resolved_at = now(), resolution_ref = 'verified in HighLevel; incident INC-123'
    WHERE effect_key = '<64-character effect digest>' AND resolved_at IS NULL;
    GET DIAGNOSTICS effect_rows = ROW_COUNT;
    IF effect_rows <> 1 THEN RAISE EXCEPTION 'expected exactly one unresolved effect row, updated %', effect_rows; END IF;
END
$reconcile$;
```

For a verified non-write instead, resolve the exact effect row and delete exactly one matching `UNKNOWN`/`IN_PROGRESS` event-ledger row in one checked transaction. This block rolls back unless both affected-row counts are one:

```sql
DO $reconcile$
DECLARE event_rows INTEGER; effect_rows INTEGER;
BEGIN
    UPDATE workflow_external_effect_reconciliations
    SET resolved_at = now(), resolution_ref = 'verified no write in HighLevel; incident INC-123'
    WHERE effect_key = '<64-character effect digest>' AND resolved_at IS NULL;
    GET DIAGNOSTICS effect_rows = ROW_COUNT;
    IF effect_rows <> 1 THEN RAISE EXCEPTION 'expected exactly one unresolved effect row, updated %', effect_rows; END IF;

    DELETE FROM workflow_ghl_opportunity_upserts
    WHERE event_key = '<64-character event digest>' AND state IN ('UNKNOWN', 'IN_PROGRESS');
    GET DIAGNOSTICS event_rows = ROW_COUNT;
    IF event_rows <> 1 THEN RAISE EXCEPTION 'expected exactly one pending event row, deleted %', event_rows; END IF;
END
$reconcile$;
```

Only then may that source event be retried. If the database shows a prior success result, do not delete it to force a new CRM call.

The `resolution_ref` must not contain contact details, provider response bodies, or credentials. Keep the CRM inspection and row resolution in the same operator incident record.
- Successful and unknown event rows have no automatic retention job. Keep success rows longer than the source's replay horizon; deleting a success row can allow the old event to write again. At most 10 pages of 100 opportunities are searched. The module uses the provider's total/next-page metadata and increments the documented page parameter; it does not follow provider-returned URLs. If the safety ceiling is reached, it stops without writing. Provider rate-limit scheduling across workers remains the caller's responsibility.
- There is no CRM account, Location, pipeline/stage mapping, or token configured in the current portfolio. No live opportunity was created or changed. `tests/integration/ghl_opportunity_upsert_postgres.py` exercises the real Compose PostgreSQL event ledger and effect guard with synthetic HTTP transport. It verifies one search/create for overlapping runs, success replay cache, changed-input hold, and unknown-write reconciliation; the harness removes its rows. This coordinates only callers using the same database and is not live HighLevel evidence.

## Composition boundary

The contact upsert and opportunity upsert are separate instances. The caller must pass the verified `contact_id` output of `crm.ghl_upsert_contact` as `opportunity.contactId` and supply account-owned pipeline/stage IDs. This module is cataloged and callable, but intentionally not added to the lead workflow graph before the user has a GHL Location and approves its pipeline mapping.

## Evidence and references

- Synthetic tests cover create, update, zero/one/multiple match behavior, pagination, no-network modes, required source event identity, success replay cache, changed-input conflict, PII-suppressed result shape, auth failures, and retry/unknown outcome boundaries.
- Official API references: [Search Opportunity](https://marketplace.gohighlevel.com/docs/ghl/opportunities/search-opportunity), [Create Opportunity](https://marketplace.gohighlevel.com/docs/ghl/opportunities/create-opportunity/), and [Update Opportunity](https://marketplace.gohighlevel.com/docs/ghl/opportunities/update-opportunity/).
