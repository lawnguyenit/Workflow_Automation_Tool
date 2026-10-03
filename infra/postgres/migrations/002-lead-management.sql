-- Re-runnable addition of the lead-management reference table.
BEGIN;

CREATE TABLE IF NOT EXISTS workflow_leads (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system       TEXT NOT NULL,
    source_account      TEXT NOT NULL,
    source_event_id     TEXT NOT NULL,
    lead_data           JSONB NOT NULL,
    payload_fingerprint CHAR(64) NOT NULL,
    workflow_id         TEXT NOT NULL,
    run_id              TEXT NOT NULL,
    correlation_id      TEXT NOT NULL,
    attempt             INTEGER NOT NULL CHECK (attempt >= 1),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT workflow_leads_source_key UNIQUE (source_system, source_account, source_event_id),
    CONSTRAINT workflow_leads_payload_object CHECK (jsonb_typeof(lead_data) = 'object')
);

INSERT INTO workflow_schema_version (component, version)
VALUES ('workflow_leads', 1)
ON CONFLICT (component) DO UPDATE
SET version = GREATEST(workflow_schema_version.version, EXCLUDED.version),
    installed_at = CASE
        WHEN workflow_schema_version.version < EXCLUDED.version THEN now()
        ELSE workflow_schema_version.installed_at
    END;

COMMIT;
