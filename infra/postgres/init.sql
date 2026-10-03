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

CREATE TABLE IF NOT EXISTS workflow_lead_followup_control (
    subject_key       CHAR(64) PRIMARY KEY CHECK (subject_key ~ '^[a-f0-9]{64}$'),
    state             TEXT NOT NULL CHECK (state IN (
                          'ACTIVE', 'PAUSED', 'STOPPED_REPLIED', 'STOPPED_BOOKED', 'STOPPED_OPTED_OUT'
                      )),
    stopped_reason    TEXT CHECK (stopped_reason IS NULL OR stopped_reason IN ('REPLIED', 'BOOKED', 'OPTED_OUT')),
    pause_event_key   CHAR(64) CHECK (pause_event_key IS NULL OR pause_event_key ~ '^[a-f0-9]{64}$'),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT workflow_lead_followup_state_consistency CHECK (
        (state = 'PAUSED' AND pause_event_key IS NOT NULL AND stopped_reason IS NULL)
        OR (state IN ('STOPPED_REPLIED', 'STOPPED_BOOKED', 'STOPPED_OPTED_OUT')
            AND pause_event_key IS NULL AND stopped_reason IS NOT NULL)
        OR (state = 'ACTIVE' AND pause_event_key IS NULL AND stopped_reason IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS workflow_lead_followup_events (
    event_key            CHAR(64) PRIMARY KEY CHECK (event_key ~ '^[a-f0-9]{64}$'),
    subject_key          CHAR(64) NOT NULL CHECK (subject_key ~ '^[a-f0-9]{64}$'),
    payload_fingerprint  CHAR(64) NOT NULL CHECK (payload_fingerprint ~ '^[a-f0-9]{64}$'),
    event_type           TEXT NOT NULL CHECK (event_type IN (
                             'FOLLOWUP_ENROLLED', 'REPLIED', 'BOOKED', 'OPTED_OUT',
                             'MANUAL_PAUSE', 'MANUAL_RESUME'
                         )),
    outcome              TEXT NOT NULL CHECK (outcome IN (
                             'ENROLLED', 'REPLIED', 'BOOKED', 'OPTED_OUT', 'PAUSED',
                             'PAUSE_REPLACED', 'RESUMED', 'STALE_RESUME_REVIEW',
                             'ALREADY_TERMINAL', 'NO_STATE_CHANGE'
                         )),
    state_after          TEXT NOT NULL CHECK (state_after IN (
                             'NOT_ENROLLED', 'ACTIVE', 'PAUSED', 'STOPPED_REPLIED',
                             'STOPPED_BOOKED', 'STOPPED_OPTED_OUT'
                         )),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS workflow_lead_followup_events_subject_created
    ON workflow_lead_followup_events (subject_key, created_at DESC);

CREATE TABLE IF NOT EXISTS workflow_source_poll_checkpoints (
    key_hash          CHAR(64) PRIMARY KEY CHECK (key_hash ~ '^[a-f0-9]{64}$'),
    cursor_at         TIMESTAMPTZ,
    lease_owner_id    TEXT CHECK (lease_owner_id IS NULL OR length(lease_owner_id) BETWEEN 1 AND 100),
    lease_token       UUID,
    lease_expires_at  TIMESTAMPTZ,
    last_action_owner_id TEXT CHECK (last_action_owner_id IS NULL OR length(last_action_owner_id) BETWEEN 1 AND 100),
    last_action_token UUID,
    last_action      TEXT CHECK (last_action IS NULL OR last_action IN ('COMMITTED', 'RELEASED')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT workflow_source_poll_lease_consistency CHECK (
        (lease_owner_id IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)
        OR (lease_owner_id IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
    ),
    CONSTRAINT workflow_source_poll_last_action_consistency CHECK (
        (last_action_owner_id IS NULL AND last_action_token IS NULL AND last_action IS NULL)
        OR (last_action_owner_id IS NOT NULL AND last_action_token IS NOT NULL
            AND last_action IN ('COMMITTED', 'RELEASED'))
    )
);

CREATE INDEX IF NOT EXISTS workflow_source_poll_checkpoints_lease_expiry
    ON workflow_source_poll_checkpoints (lease_expires_at)
    WHERE lease_expires_at IS NOT NULL;

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
VALUES ('workflow_invoices', 1), ('notification_outbox', 1), ('workflow_leads', 1),
       ('workflow_lead_qualification_ledger', 1), ('workflow_lead_followup_suppression', 1),
       ('workflow_source_poll_checkpoint', 1), ('workflow_run_incidents', 1)
ON CONFLICT (component) DO UPDATE
SET version = GREATEST(workflow_schema_version.version, EXCLUDED.version),
    installed_at = CASE
        WHEN workflow_schema_version.version < EXCLUDED.version THEN now()
        ELSE workflow_schema_version.installed_at
    END;

COMMIT;
