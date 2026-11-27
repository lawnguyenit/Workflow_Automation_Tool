from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg


class SourcePollCheckpointError(RuntimeError):
    code = "SOURCE_POLL_STATE_UNAVAILABLE"


class SourcePollCheckpointNotConfigured(SourcePollCheckpointError):
    code = "SOURCE_POLL_STATE_NOT_CONFIGURED"


class SourcePollCheckpointUnavailable(SourcePollCheckpointError):
    code = "SOURCE_POLL_STATE_UNAVAILABLE"


class PostgresSourcePollCheckpoint:
    """Persist a timestamp checkpoint and fenced lease without source PII."""

    def __init__(self, *, dsn: str | None = None, connection_factory: Callable[..., Any] | None = None):
        self._dsn = dsn if dsn is not None else os.getenv("DATABASE_URL")
        self._connect = connection_factory or (
            lambda database_url: psycopg.connect(database_url, connect_timeout=5)
        )

    def acquire(self, *, key_hash: str, owner_id: str, lease_seconds: int) -> dict[str, Any]:
        dsn = self._require_dsn()
        try:
            with self._connect(dsn) as connection:
                with connection.cursor() as cursor:
                    self._set_transaction_timeouts(cursor)
                    cursor.execute(
                        "INSERT INTO workflow_source_poll_checkpoints (key_hash) VALUES (%s) "
                        "ON CONFLICT (key_hash) DO NOTHING",
                        (key_hash,),
                    )
                    cursor.execute(
                        "SELECT cursor_at, lease_owner_id, lease_token, lease_expires_at, "
                        "clock_timestamp() FROM workflow_source_poll_checkpoints "
                        "WHERE key_hash = %s FOR UPDATE",
                        (key_hash,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise SourcePollCheckpointUnavailable("checkpoint row disappeared")
                    cursor_at, active_owner, active_token, expires_at, now = row
                    if active_token is not None and expires_at > now:
                        if active_owner != owner_id:
                            return {"state": "BUSY", "cursor_at": self._format(cursor_at)}
                        cursor.execute(
                            "UPDATE workflow_source_poll_checkpoints SET lease_expires_at = "
                            "clock_timestamp() + (%s * interval '1 second'), updated_at = clock_timestamp() "
                            "WHERE key_hash = %s RETURNING cursor_at, lease_token, lease_expires_at",
                            (lease_seconds, key_hash),
                        )
                        cursor_at, active_token, expires_at = cursor.fetchone()
                        return {"state": "ACQUIRED", "cursor_at": self._format(cursor_at),
                                "lease_token": str(active_token), "lease_expires_at": self._format(expires_at)}

                    cursor.execute(
                        "UPDATE workflow_source_poll_checkpoints SET lease_owner_id = %s, "
                        "lease_token = gen_random_uuid(), lease_expires_at = "
                        "clock_timestamp() + (%s * interval '1 second'), updated_at = clock_timestamp() "
                        "WHERE key_hash = %s RETURNING cursor_at, lease_token, lease_expires_at",
                        (owner_id, lease_seconds, key_hash),
                    )
                    cursor_at, active_token, expires_at = cursor.fetchone()
                    return {"state": "ACQUIRED", "cursor_at": self._format(cursor_at),
                            "lease_token": str(active_token), "lease_expires_at": self._format(expires_at)}
        except SourcePollCheckpointError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise SourcePollCheckpointUnavailable("could not acquire source poll lease") from exc

    def commit(self, *, key_hash: str, owner_id: str, lease_token: UUID,
               cursor_at: datetime) -> dict[str, Any]:
        dsn = self._require_dsn()
        try:
            with self._connect(dsn) as connection:
                with connection.cursor() as cursor:
                    self._set_transaction_timeouts(cursor)
                    cursor.execute(
                        "SELECT cursor_at, lease_owner_id, lease_token, lease_expires_at, "
                        "last_action_owner_id, last_action_token, last_action, "
                        "clock_timestamp() FROM workflow_source_poll_checkpoints "
                        "WHERE key_hash = %s FOR UPDATE",
                        (key_hash,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        return {"state": "LEASE_LOST"}
                    (current_cursor, current_owner, current_token, expires_at,
                     last_owner, last_token, last_action, now) = row
                    if (last_owner == owner_id and last_token == lease_token
                            and last_action == "COMMITTED"):
                        return {"state": "COMMITTED", "cursor_at": self._format(current_cursor)}
                    if (current_owner != owner_id or current_token != lease_token
                            or expires_at is None or expires_at <= now):
                        return {"state": "LEASE_LOST"}
                    effective_cursor = max(current_cursor, cursor_at) if current_cursor else cursor_at
                    cursor.execute(
                        "UPDATE workflow_source_poll_checkpoints SET cursor_at = %s, "
                        "lease_owner_id = NULL, lease_token = NULL, lease_expires_at = NULL, "
                        "last_action_owner_id = %s, last_action_token = %s, last_action = 'COMMITTED', "
                        "updated_at = clock_timestamp() WHERE key_hash = %s",
                        (effective_cursor, owner_id, lease_token, key_hash),
                    )
                    return {"state": "COMMITTED", "cursor_at": self._format(effective_cursor)}
        except SourcePollCheckpointError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise SourcePollCheckpointUnavailable("could not commit source poll checkpoint") from exc

    def renew(self, *, key_hash: str, owner_id: str, lease_token: UUID,
              lease_seconds: int) -> dict[str, Any]:
        dsn = self._require_dsn()
        try:
            with self._connect(dsn) as connection:
                with connection.cursor() as cursor:
                    self._set_transaction_timeouts(cursor)
                    cursor.execute(
                        "UPDATE workflow_source_poll_checkpoints SET lease_expires_at = "
                        "clock_timestamp() + (%s * interval '1 second'), updated_at = clock_timestamp() "
                        "WHERE key_hash = %s AND lease_owner_id = %s AND lease_token = %s "
                        "AND lease_expires_at > clock_timestamp() RETURNING lease_expires_at",
                        (lease_seconds, key_hash, owner_id, lease_token),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        return {"state": "LEASE_LOST"}
                    return {"state": "RENEWED", "lease_expires_at": self._format(row[0])}
        except SourcePollCheckpointError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise SourcePollCheckpointUnavailable("could not renew source poll lease") from exc

    def release(self, *, key_hash: str, owner_id: str, lease_token: UUID) -> dict[str, Any]:
        dsn = self._require_dsn()
        try:
            with self._connect(dsn) as connection:
                with connection.cursor() as cursor:
                    self._set_transaction_timeouts(cursor)
                    cursor.execute(
                        "SELECT cursor_at, lease_owner_id, lease_token, last_action_owner_id, "
                        "last_action_token, last_action FROM workflow_source_poll_checkpoints "
                        "WHERE key_hash = %s FOR UPDATE",
                        (key_hash,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        return {"state": "LEASE_LOST"}
                    cursor_at, active_owner, active_token, last_owner, last_token, last_action = row
                    if last_owner == owner_id and last_token == lease_token and last_action == "RELEASED":
                        return {"state": "RELEASED", "cursor_at": self._format(cursor_at)}
                    if active_owner != owner_id or active_token != lease_token:
                        return {"state": "LEASE_LOST"}
                    cursor.execute(
                        "UPDATE workflow_source_poll_checkpoints SET lease_owner_id = NULL, "
                        "lease_token = NULL, lease_expires_at = NULL, last_action_owner_id = %s, "
                        "last_action_token = %s, last_action = 'RELEASED', updated_at = clock_timestamp() "
                        "WHERE key_hash = %s",
                        (owner_id, lease_token, key_hash),
                    )
                    return {"state": "RELEASED", "cursor_at": self._format(cursor_at)}
        except SourcePollCheckpointError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise SourcePollCheckpointUnavailable("could not release source poll lease") from exc

    def _require_dsn(self) -> str:
        if not isinstance(self._dsn, str) or not self._dsn.strip():
            raise SourcePollCheckpointNotConfigured("DATABASE_URL is required for durable source poll state")
        return self._dsn

    @staticmethod
    def _format(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _set_transaction_timeouts(cursor: Any) -> None:
        cursor.execute("SET LOCAL lock_timeout = '3s'")
        cursor.execute("SET LOCAL statement_timeout = '10s'")
