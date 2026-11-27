-- Durable, PII-minimized workflow failure records for operator review.
BEGIN;

CREATE TABLE IF NOT EXISTS workflow_run_incidents (
    incident_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_key          CHAR(64) NOT NULL UNIQUE CHECK (event_key ~ '^[a-f0-9]{64}$'),
    workflow_id        TEXT NOT NULL CHECK (length(workflow_id) BETWEEN 1 AND 120),
    attempt            INTEGER NOT NULL CHECK (attempt >= 1),
    mode               TEXT NOT NULL CHECK (mode IN ('MOCK', 'REAL')),
    status             TEXT NOT NULL CHECK (status IN ('PARTIAL', 'FAILURE', 'REVIEW_REQUIRED')),
    diagnostics        JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
    diagnostic_fingerprint CHAR(64) NOT NULL CHECK (diagnostic_fingerprint ~ '^[a-f0-9]{64}$'),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at        TIMESTAMPTZ,
    resolution_code    TEXT CHECK (resolution_code IS NULL OR resolution_code IN (
                           'RETRY_COMPLETED', 'PROVIDER_STATE_CONFIRMED', 'MANUAL_REPAIR',
                           'DUPLICATE_CONFIRMED', 'DISMISSED'
                       )),
    CONSTRAINT workflow_run_incident_resolution_pair CHECK (
        (resolved_at IS NULL AND resolution_code IS NULL)
        OR (resolved_at IS NOT NULL AND resolution_code IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS workflow_run_incidents_open
    ON workflow_run_incidents (created_at DESC)
    WHERE resolved_at IS NULL;

INSERT INTO workflow_schema_version (component, version)
VALUES ('workflow_run_incidents', 1)
ON CONFLICT (component) DO UPDATE
SET version = GREATEST(workflow_schema_version.version, EXCLUDED.version),
    installed_at = CASE
        WHEN workflow_schema_version.version < EXCLUDED.version THEN now()
        ELSE workflow_schema_version.installed_at
    END;

COMMIT;
