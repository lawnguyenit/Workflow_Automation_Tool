-- Durable, PII-minimized state for lead follow-up eligibility and stop events.
BEGIN;

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

INSERT INTO workflow_schema_version (component, version)
VALUES ('workflow_lead_followup_suppression', 1)
ON CONFLICT (component) DO UPDATE
SET version = GREATEST(workflow_schema_version.version, EXCLUDED.version),
    installed_at = CASE
        WHEN workflow_schema_version.version < EXCLUDED.version THEN now()
        ELSE workflow_schema_version.installed_at
    END;

COMMIT;
