-- Durable result cache and reconciliation state for one GHL contact source event.
BEGIN;

CREATE TABLE IF NOT EXISTS workflow_ghl_contact_upserts (
    event_key           CHAR(64) PRIMARY KEY CHECK (event_key ~ '^[a-f0-9]{64}$'),
    input_fingerprint   CHAR(64) NOT NULL CHECK (input_fingerprint ~ '^[a-f0-9]{64}$'),
    state               TEXT NOT NULL CHECK (state IN ('IN_PROGRESS', 'SUCCEEDED', 'UNKNOWN')),
    result              JSONB,
    error_code          TEXT CHECK (error_code IS NULL OR length(error_code) BETWEEN 1 AND 100),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT workflow_ghl_contact_upsert_result_state CHECK (
        (state = 'SUCCEEDED' AND jsonb_typeof(result) = 'object' AND error_code IS NULL)
        OR (state <> 'SUCCEEDED' AND result IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS workflow_ghl_contact_upserts_pending
    ON workflow_ghl_contact_upserts (created_at)
    WHERE state IN ('IN_PROGRESS', 'UNKNOWN');

INSERT INTO workflow_schema_version (component, version)
VALUES ('workflow_ghl_contact_upserts', 1)
ON CONFLICT (component) DO UPDATE
SET version = GREATEST(workflow_schema_version.version, EXCLUDED.version),
    installed_at = CASE
        WHEN workflow_schema_version.version < EXCLUDED.version THEN now()
        ELSE workflow_schema_version.installed_at
    END;

COMMIT;
