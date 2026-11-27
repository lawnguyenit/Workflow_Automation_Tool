-- Schema for the n8n invoice-intake workflows (workflows/). Repeatable: safe to run twice.
-- Docker Compose mounts this file as /docker-entrypoint-initdb.d/10-workflow-init.sql, so it
-- runs once, on an empty data volume, in the application database (not the n8n database).
-- The old Python-era schema is in legacy/python-modules/infra/postgres/init.sql.
BEGIN;

CREATE TABLE IF NOT EXISTS schema_version (
    component       TEXT PRIMARY KEY,
    version         INTEGER NOT NULL,
    installed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per stored invoice PDF. idempotency_key = sha256(message id + "\n" + PDF sha256),
-- computed in code/src/duplicate-key.js. The unique constraint is the real duplicate guard.
-- payload_fingerprint = sha256 of the normalized fields (code/src/normalize.js); a second
-- insert with the same key and a different fingerprint is reported as CONFLICT, not written.
CREATE TABLE IF NOT EXISTS invoices (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key      CHAR(64) NOT NULL CHECK (idempotency_key ~ '^[a-f0-9]{64}$'),
    source_system        TEXT NOT NULL DEFAULT 'gmail',
    source_message_id    TEXT NOT NULL CHECK (length(source_message_id) BETWEEN 1 AND 256),
    source_attachment_id TEXT NOT NULL CHECK (length(source_attachment_id) BETWEEN 1 AND 256),
    filename             TEXT CHECK (filename IS NULL OR length(filename) <= 255),
    invoice_number       TEXT NOT NULL CHECK (length(invoice_number) BETWEEN 1 AND 200),
    sender               TEXT CHECK (sender IS NULL OR length(sender) <= 500),
    amount               NUMERIC NOT NULL,
    currency             CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    invoice_data         JSONB NOT NULL CHECK (jsonb_typeof(invoice_data) = 'object'),
    payload_fingerprint  CHAR(64) NOT NULL CHECK (payload_fingerprint ~ '^[a-f0-9]{64}$'),
    n8n_execution_id     TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT invoices_idempotency_key UNIQUE (idempotency_key)
);

-- Written by workflows/error-handler. No email body, subject, sender or item data:
-- only ids, node name, error class and a masked, bounded message (code/src/incident.js).
CREATE TABLE IF NOT EXISTS incident_log (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    workflow_id      TEXT CHECK (workflow_id IS NULL OR length(workflow_id) <= 64),
    workflow_name    TEXT CHECK (workflow_name IS NULL OR length(workflow_name) <= 200),
    execution_id     TEXT CHECK (execution_id IS NULL OR length(execution_id) <= 64),
    failed_node      TEXT CHECK (failed_node IS NULL OR length(failed_node) <= 200),
    error_name       TEXT CHECK (error_name IS NULL OR length(error_name) <= 100),
    error_message    TEXT CHECK (error_message IS NULL OR length(error_message) <= 300),
    execution_mode   TEXT CHECK (execution_mode IS NULL OR length(execution_mode) <= 32),
    acknowledged_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS incident_log_open
    ON incident_log (occurred_at DESC)
    WHERE acknowledged_at IS NULL;

-- Daily AI-call counter. workflows/sub/extract-invoice reserves one call with
--   INSERT .. ON CONFLICT (call_date, provider) DO UPDATE SET calls = calls + 1 WHERE calls < limit
-- before calling Gemini; no row returned = cap reached, no call made.
-- call_date uses the database's current_date (set the server/session TimeZone deliberately).
-- It counts requests, not tokens or money.
CREATE TABLE IF NOT EXISTS ai_call_ledger (
    call_date   DATE NOT NULL,
    provider    TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 50),
    calls       INTEGER NOT NULL CHECK (calls >= 0),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (call_date, provider)
);

INSERT INTO schema_version (component, version)
VALUES ('invoice_intake', 1)
ON CONFLICT (component) DO UPDATE SET version = EXCLUDED.version, installed_at = now();

COMMIT;
