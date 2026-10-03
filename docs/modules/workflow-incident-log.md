# Workflow incident log

## Identity

- **Capability / stage:** Cross-cutting infrastructure; durable incident metadata and operator acknowledgement.
- **Implementation:** `PostgresWorkflowIncidentLog` in `src/workflow_automation/infrastructure/postgres_workflow_incidents.py`.
- **Status:** Implemented; unit/API and synthetic Compose PostgreSQL checks passed. No production alerting or automatic recovery is included.
- **Runtime:** FastAPI workflow/module routes and PostgreSQL; authenticated operator routes use the existing shared `WORKFLOW_API_KEY`.

## Behavior

- `MOCK` and `REAL` runs with overall `PARTIAL`, `FAILURE`, or `REVIEW_REQUIRED` create one incident keyed by a SHA-256 digest of workflow ID, run ID, and attempt. Same event and same sanitized diagnostics are idempotent; changed diagnostics under that event are held as `INCIDENT_EVENT_CONFLICT`.
- Only a bounded list of step/module IDs, status, error code/category, and retryability is stored. Payloads, business data, error messages/details, secrets, and raw run/correlation IDs are excluded. A separate diagnostic fingerprint permits conflict detection without storing the diagnostics' source data.
- Successful runs and `DRY_RUN`/`REPLAY` do not create incidents. Recording is best effort: if PostgreSQL is unavailable, the original run result remains authoritative and a safe warning is emitted. Durability is therefore not guaranteed during a database outage.
- `GET /v1/incidents` lists at most 100 items per request (open incidents by default); `GET /v1/incidents/{id}` reads one item. Both require a bearer key. There is no pagination or retention job yet.
- `POST /v1/incidents/{id}/resolve` accepts only `RETRY_COMPLETED`, `PROVIDER_STATE_CONFIRMED`, `MANUAL_REPAIR`, `DUPLICATE_CONFIRMED`, or `DISMISSED`. This records an operator acknowledgement; it does not perform a retry or prove provider state. Resolution is one-way.

## Safety, recovery, and limits

- Same-workflow/run/attempt concurrency converges on one row through a unique database constraint. Conflicting result data is not silently overwritten.
- There is no retry scheduler, dead-letter worker, external Slack/email alert, pagination, automatic retention/deletion, dashboard, or backup/restore evidence. An operator must inspect the source system and workflow before marking an incident resolved.
- Rows contain workflow/module IDs and safe diagnostic enums and remain in PostgreSQL until owner-managed retention is added. The list endpoint is authenticated by one shared key, not per-user roles; keep the service on its private loopback network.
- Migration: apply `infra/postgres/migrations/007-workflow-incident-log.sql`; fresh databases receive the equivalent schema from `infra/postgres/init.sql`. No data migration is needed from earlier releases. Rollback is not automated; dropping the table would discard incident history.
- **Evidence:** five unit tests, API route tests, full test suite, plus a synthetic-only integration against the running local Compose PostgreSQL and FastAPI app. No external provider or real prospect data was used.
