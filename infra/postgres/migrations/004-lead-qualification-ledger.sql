-- Durable idempotency and bounded daily request count for REAL lead qualification.
BEGIN;

CREATE TABLE IF NOT EXISTS workflow_lead_qualification_calls (
    event_key               CHAR(64) PRIMARY KEY CHECK (event_key ~ '^[a-f0-9]{64}$'),
    input_fingerprint       CHAR(64) NOT NULL CHECK (input_fingerprint ~ '^[a-f0-9]{64}$'),
    criteria_fingerprint    CHAR(64) NOT NULL CHECK (criteria_fingerprint ~ '^[a-f0-9]{64}$'),
    provider_model          TEXT NOT NULL CHECK (length(provider_model) BETWEEN 1 AND 200),
    prompt_version          TEXT NOT NULL CHECK (length(prompt_version) BETWEEN 1 AND 100),
    state                   TEXT NOT NULL CHECK (state IN ('IN_PROGRESS', 'COMPLETED', 'UNKNOWN')),
    result                  JSONB,
    error_code              TEXT CHECK (error_code IS NULL OR length(error_code) BETWEEN 1 AND 100),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT workflow_lead_qualification_result_state CHECK (
        (state = 'COMPLETED' AND jsonb_typeof(result) = 'object' AND error_code IS NULL)
        OR (state <> 'COMPLETED' AND result IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS workflow_lead_qualification_daily_budget (
    call_day_utc       DATE NOT NULL,
    model_key          CHAR(64) NOT NULL CHECK (model_key ~ '^[a-f0-9]{64}$'),
    attempted_calls    INTEGER NOT NULL CHECK (attempted_calls >= 0),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (call_day_utc, model_key)
);

CREATE INDEX IF NOT EXISTS workflow_lead_qualification_unknown
    ON workflow_lead_qualification_calls (created_at)
    WHERE state IN ('IN_PROGRESS', 'UNKNOWN');

INSERT INTO workflow_schema_version (component, version)
VALUES ('workflow_lead_qualification_ledger', 1)
ON CONFLICT (component) DO UPDATE
SET version = GREATEST(workflow_schema_version.version, EXCLUDED.version),
    installed_at = CASE
        WHEN workflow_schema_version.version < EXCLUDED.version THEN now()
        ELSE workflow_schema_version.installed_at
    END;

COMMIT;
