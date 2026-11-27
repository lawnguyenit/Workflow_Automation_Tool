from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any, TypeVar

import psycopg


T = TypeVar("T")
_EFFECT_KEY = re.compile(r"^[a-f0-9]{64}$")


class EffectGuardError(RuntimeError):
    pass


class EffectGuardNotConfigured(EffectGuardError):
    pass


class EffectGuardUnavailable(EffectGuardError):
    def __init__(self, *, external_call_started: bool) -> None:
        self.external_call_started = external_call_started
        super().__init__("PostgreSQL effect guard is unavailable")


class EffectGuardBusy(EffectGuardError):
    pass


class EffectReconciliationPending(EffectGuardError):
    pass


class PostgresExternalEffectGuard:
    """Serialize one external effect key across API workers and hold unknown writes.

    PostgreSQL session advisory locks are scoped to a connection and released if a
    worker dies. A durable reconciliation row prevents retries after an uncertain
    provider result until an operator resolves it.
    """

    def __init__(self, *, dsn: str | None = None, connection_factory: Callable[..., Any] | None = None) -> None:
        self._dsn = dsn if dsn is not None else os.getenv("DATABASE_URL")
        self._connect = connection_factory or psycopg.connect

    def run(
        self,
        *,
        effect_key: str,
        effect_type: str,
        correlation_id: str,
        operation: Callable[[], T],
        uncertain_error_code: Callable[[T], str | None],
    ) -> T:
        if not isinstance(effect_key, str) or not _EFFECT_KEY.fullmatch(effect_key):
            raise EffectGuardNotConfigured("effect key must be a SHA-256 digest")
        if not isinstance(self._dsn, str) or not self._dsn.strip():
            raise EffectGuardNotConfigured("DATABASE_URL is required for guarded external writes")

        external_call_started = False
        try:
            with self._connect(self._dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (effect_key,))
                    lock_acquired = bool(cursor.fetchone()[0])
                    if not lock_acquired:
                        raise EffectGuardBusy("another worker is processing this external effect")
                    try:
                        cursor.execute(
                            """SELECT 1 FROM workflow_external_effect_reconciliations
                               WHERE effect_key = %s AND resolved_at IS NULL""",
                            (effect_key,),
                        )
                        if cursor.fetchone() is not None:
                            raise EffectReconciliationPending("an earlier write needs reconciliation")

                        cursor.execute(
                            """INSERT INTO workflow_external_effect_reconciliations
                                   (effect_key, effect_type, correlation_id, error_code)
                               VALUES (%s, %s, %s, 'IN_PROGRESS')
                               ON CONFLICT (effect_key) DO UPDATE SET
                                   effect_type = EXCLUDED.effect_type,
                                   correlation_id = EXCLUDED.correlation_id,
                                   error_code = EXCLUDED.error_code,
                                   created_at = now(),
                                   last_seen_at = now(),
                                   resolved_at = NULL
                               WHERE workflow_external_effect_reconciliations.resolved_at IS NOT NULL
                               RETURNING effect_key""",
                            (effect_key, effect_type, correlation_id),
                        )
                        if cursor.fetchone() is None:
                            raise EffectReconciliationPending("an external effect claim is already pending")

                        # Persist the claim before provider I/O. If the worker dies after
                        # a write but before recording its result, later calls still stop.
                        external_call_started = True
                        result = operation()
                        error_code = uncertain_error_code(result)
                        if error_code:
                            cursor.execute(
                                """UPDATE workflow_external_effect_reconciliations
                                   SET error_code = %s, last_seen_at = now()
                                   WHERE effect_key = %s AND resolved_at IS NULL""",
                                (error_code, effect_key),
                            )
                        else:
                            cursor.execute(
                                """DELETE FROM workflow_external_effect_reconciliations
                                   WHERE effect_key = %s AND resolved_at IS NULL""",
                                (effect_key,),
                            )
                        return result
                    finally:
                        cursor.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (effect_key,))
                        unlocked = cursor.fetchone()
                        if unlocked is None or unlocked[0] is not True:
                            raise EffectGuardUnavailable(external_call_started=external_call_started)
        except (EffectGuardBusy, EffectReconciliationPending, EffectGuardNotConfigured,
                EffectGuardUnavailable):
            raise
        except psycopg.errors.UndefinedTable as exc:
            raise EffectGuardNotConfigured("apply the external-effect reconciliation migration first") from exc
        except (psycopg.Error, OSError) as exc:
            raise EffectGuardUnavailable(external_call_started=external_call_started) from exc
