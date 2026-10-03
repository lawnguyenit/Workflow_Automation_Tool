-- Repeatable table for external writes whose provider outcome cannot be verified.
BEGIN;

CREATE TABLE IF NOT EXISTS workflow_external_effect_reconciliations (
    effect_key       CHAR(64) PRIMARY KEY CHECK (effect_key ~ '^[a-f0-9]{64}$'),
    effect_type      TEXT NOT NULL CHECK (length(effect_type) BETWEEN 1 AND 100),
    correlation_id   TEXT NOT NULL CHECK (length(correlation_id) BETWEEN 1 AND 200),
    error_code       TEXT NOT NULL CHECK (length(error_code) BETWEEN 1 AND 100),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at      TIMESTAMPTZ,
    resolution_ref   TEXT
);

CREATE INDEX IF NOT EXISTS workflow_external_effect_reconciliation_pending
    ON workflow_external_effect_reconciliations (created_at)
    WHERE resolved_at IS NULL;

INSERT INTO workflow_schema_version (component, version)
VALUES ('workflow_external_effect_reconciliations', 1)
ON CONFLICT (component) DO UPDATE
SET version = GREATEST(workflow_schema_version.version, EXCLUDED.version),
    installed_at = CASE
        WHEN workflow_schema_version.version < EXCLUDED.version THEN now()
        ELSE workflow_schema_version.installed_at
    END;

COMMIT;
