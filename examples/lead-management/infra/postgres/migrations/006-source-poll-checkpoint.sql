-- Durable, source-scoped polling checkpoint with lease fencing.
BEGIN;

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
        OR (last_action_owner_id IS NOT NULL AND last_action_token IS NOT NULL AND last_action IS NOT NULL)
    )
);

ALTER TABLE workflow_source_poll_checkpoints
    ADD COLUMN IF NOT EXISTS last_action_owner_id TEXT,
    ADD COLUMN IF NOT EXISTS last_action_token UUID,
    ADD COLUMN IF NOT EXISTS last_action TEXT;

ALTER TABLE workflow_source_poll_checkpoints
    DROP CONSTRAINT IF EXISTS workflow_source_poll_last_action_consistency;

ALTER TABLE workflow_source_poll_checkpoints
    ADD CONSTRAINT workflow_source_poll_last_action_consistency CHECK (
        (last_action_owner_id IS NULL AND last_action_token IS NULL AND last_action IS NULL)
        OR (last_action_owner_id IS NOT NULL AND last_action_token IS NOT NULL
            AND last_action IN ('COMMITTED', 'RELEASED'))
    );

ALTER TABLE workflow_source_poll_checkpoints
    DROP CONSTRAINT IF EXISTS workflow_source_poll_lease_consistency;

ALTER TABLE workflow_source_poll_checkpoints
    ADD CONSTRAINT workflow_source_poll_lease_consistency CHECK (
        (lease_owner_id IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)
        OR (lease_owner_id IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
    );

CREATE INDEX IF NOT EXISTS workflow_source_poll_checkpoints_lease_expiry
    ON workflow_source_poll_checkpoints (lease_expires_at)
    WHERE lease_expires_at IS NOT NULL;

INSERT INTO workflow_schema_version (component, version)
VALUES ('workflow_source_poll_checkpoint', 1)
ON CONFLICT (component) DO UPDATE
SET version = GREATEST(workflow_schema_version.version, EXCLUDED.version),
    installed_at = CASE
        WHEN workflow_schema_version.version < EXCLUDED.version THEN now()
        ELSE workflow_schema_version.installed_at
    END;

COMMIT;
