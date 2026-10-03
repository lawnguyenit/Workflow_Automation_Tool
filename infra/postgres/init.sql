-- Repeatable initialization for the synthetic Intelligent Document Intake portfolio.
-- Apply through a migration/deployment identity; runtime credentials should have
-- only the table permissions required by the workflow.
BEGIN;

CREATE TABLE IF NOT EXISTS workflow_schema_version (
    component       TEXT PRIMARY KEY,
    version         INTEGER NOT NULL,
    installed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workflow_invoices (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system       TEXT NOT NULL,
    source_account      TEXT NOT NULL,
    source_message_id   TEXT NOT NULL,
    source_attachment_id TEXT NOT NULL DEFAULT '',
    invoice_data        JSONB NOT NULL,
    payload_fingerprint CHAR(64) NOT NULL,
    workflow_id         TEXT NOT NULL,
    run_id              TEXT NOT NULL,
    correlation_id      TEXT NOT NULL,
    attempt             INTEGER NOT NULL CHECK (attempt >= 1),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT workflow_invoices_source_key UNIQUE (
        source_system, source_account, source_message_id, source_attachment_id
    ),
    CONSTRAINT workflow_invoices_payload_object CHECK (jsonb_typeof(invoice_data) = 'object')
);

-- Reference lead-management events. An event key prevents a source retry from
-- creating a second intake row; this does not guarantee CRM-contact/message dedupe.
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

CREATE TABLE IF NOT EXISTS notification_outbox (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key     TEXT NOT NULL UNIQUE,
    destination         TEXT NOT NULL,
    payload             JSONB NOT NULL,
    payload_fingerprint CHAR(64) NOT NULL,
    state               TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (state IN (
                            'PENDING', 'SENDING', 'SENT', 'FAILED', 'UNKNOWN',
                            'REVIEW_REQUIRED', 'MOCKED', 'PREVIEWED'
                        )),
    attempt_count       INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    error_code          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT notification_outbox_payload_object CHECK (jsonb_typeof(payload) = 'object')
);

INSERT INTO workflow_schema_version (component, version)
VALUES ('workflow_invoices', 1), ('notification_outbox', 1), ('workflow_leads', 1),
       ('workflow_lead_qualification_ledger', 1)
ON CONFLICT (component) DO UPDATE
SET version = GREATEST(workflow_schema_version.version, EXCLUDED.version),
    installed_at = CASE
        WHEN workflow_schema_version.version < EXCLUDED.version THEN now()
        ELSE workflow_schema_version.installed_at
    END;

COMMIT;
