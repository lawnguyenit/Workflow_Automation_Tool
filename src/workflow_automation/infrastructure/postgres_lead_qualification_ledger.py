from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_CODE = re.compile(r"^[A-Z0-9_]{1,100}$")


class QualificationLedgerError(RuntimeError):
    code = "QUALIFICATION_LEDGER_UNAVAILABLE"


class QualificationLedgerNotConfigured(QualificationLedgerError):
    code = "QUALIFICATION_LEDGER_NOT_CONFIGURED"


class QualificationLedgerUnavailable(QualificationLedgerError):
    code = "QUALIFICATION_LEDGER_UNAVAILABLE"

    def __init__(self, message: str, *, external_call_started: bool = False):
        self.external_call_started = external_call_started
        super().__init__(message)


class QualificationInProgress(QualificationLedgerError):
    code = "QUALIFICATION_ALREADY_IN_PROGRESS"


class QualificationReconciliationRequired(QualificationLedgerError):
    code = "QUALIFICATION_RECONCILIATION_REQUIRED"


class QualificationInputConflict(QualificationLedgerError):
    code = "QUALIFICATION_INPUT_CONFLICT"


class QualificationBudgetExhausted(QualificationLedgerError):
    code = "QUALIFICATION_DAILY_CALL_LIMIT_REACHED"


class PostgresLeadQualificationLedger:
    """Persist one model result per stable source event and bound attempted calls per UTC day.

    A durable IN_PROGRESS/UNKNOWN row intentionally blocks automatic retries after
    a worker or provider outcome becomes ambiguous. This prevents duplicate model
    calls, but requires an operator to reconcile calls interrupted before caching.
    """

    def __init__(self, *, dsn: str | None = None, connection_factory: Callable[..., Any] | None = None):
        self._dsn = dsn if dsn is not None else os.getenv("DATABASE_URL")
        self._connect = connection_factory or psycopg.connect

    def run_once(
        self,
        *,
        event_key: str,
        input_fingerprint: str,
        criteria_fingerprint: str,
        provider_model: str,
        prompt_version: str,
        max_calls_per_day: int,
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        if (not isinstance(event_key, str) or not _DIGEST.fullmatch(event_key)
                or not isinstance(input_fingerprint, str) or not _DIGEST.fullmatch(input_fingerprint)
                or not isinstance(criteria_fingerprint, str) or not _DIGEST.fullmatch(criteria_fingerprint)):
            raise QualificationLedgerNotConfigured("qualification identity must use SHA-256 digests")
        if (isinstance(max_calls_per_day, bool) or not isinstance(max_calls_per_day, int)
                or not 1 <= max_calls_per_day <= 100_000):
            raise QualificationLedgerNotConfigured("an explicit daily provider-call limit is required")
        if (not isinstance(provider_model, str) or not 1 <= len(provider_model) <= 200
                or not isinstance(prompt_version, str) or not 1 <= len(prompt_version) <= 100):
            raise QualificationLedgerNotConfigured("provider model and prompt version must be bounded")
        if not isinstance(self._dsn, str) or not self._dsn.strip():
            raise QualificationLedgerNotConfigured("DATABASE_URL is required for REAL qualification")

        external_call_started = False
        try:
            with self._connect(self._dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (event_key,))
                    locked = bool(cursor.fetchone()[0])
                    if not locked:
                        raise QualificationInProgress("another worker is qualifying this source event")
                    try:
                        cursor.execute(
                            """SELECT input_fingerprint, criteria_fingerprint, provider_model,
                                      prompt_version, state, result
                               FROM workflow_lead_qualification_calls WHERE event_key = %s""",
                            (event_key,),
                        )
                        existing = cursor.fetchone()
                        if existing is not None:
                            old_input, old_criteria, old_model, old_prompt, state, result = existing
                            if (old_input != input_fingerprint or old_criteria != criteria_fingerprint
                                    or old_model != provider_model or old_prompt != prompt_version):
                                raise QualificationInputConflict(
                                    "source event already has a qualification for different content or criteria"
                                )
                            if state == "COMPLETED" and isinstance(result, dict):
                                return result
                            if state in {"IN_PROGRESS", "UNKNOWN"}:
                                raise QualificationReconciliationRequired(
                                    "an earlier model call needs reconciliation before retry"
                                )
                            raise QualificationLedgerUnavailable("qualification ledger has an invalid state")

                        call_day = datetime.now(UTC).date()
                        model_key = hashlib.sha256(provider_model.encode("utf-8")).hexdigest()
                        cursor.execute(
                            """INSERT INTO workflow_lead_qualification_daily_budget
                                   (call_day_utc, model_key, attempted_calls)
                               VALUES (%s, %s, 1)
                               ON CONFLICT (call_day_utc, model_key) DO UPDATE
                                  SET attempted_calls = workflow_lead_qualification_daily_budget.attempted_calls + 1,
                                      updated_at = now()
                                WHERE workflow_lead_qualification_daily_budget.attempted_calls < %s
                               RETURNING attempted_calls""",
                            (call_day, model_key, max_calls_per_day),
                        )
                        if cursor.fetchone() is None:
                            raise QualificationBudgetExhausted("daily model-call limit is exhausted")
                        cursor.execute(
                            """INSERT INTO workflow_lead_qualification_calls (
                                   event_key, input_fingerprint, criteria_fingerprint, provider_model,
                                   prompt_version, state, result, error_code)
                               VALUES (%s, %s, %s, %s, %s, 'IN_PROGRESS', NULL, NULL)""",
                            (event_key, input_fingerprint, criteria_fingerprint, provider_model, prompt_version),
                        )
                        # In autocommit mode, the budget reservation and claim are durable
                        # before the provider request begins.
                        external_call_started = True
                        result = operation()
                        if not isinstance(result, dict):
                            raise QualificationLedgerUnavailable("model result must be an object",
                                                                 external_call_started=True)
                        cursor.execute(
                            """UPDATE workflow_lead_qualification_calls
                                  SET state = 'COMPLETED', result = %s, error_code = NULL, updated_at = now()
                                WHERE event_key = %s AND state = 'IN_PROGRESS'""",
                            (Jsonb(result), event_key),
                        )
                        if cursor.rowcount != 1:
                            raise QualificationLedgerUnavailable("qualification result could not be persisted",
                                                                 external_call_started=True)
                        return result
                    except Exception as exc:
                        if external_call_started:
                            code = getattr(exc, "code", "LLM_OUTCOME_UNKNOWN")
                            if not isinstance(code, str) or not _CODE.fullmatch(code):
                                code = "LLM_OUTCOME_UNKNOWN"
                            try:
                                cursor.execute(
                                    """UPDATE workflow_lead_qualification_calls
                                          SET state = 'UNKNOWN', error_code = %s, updated_at = now()
                                        WHERE event_key = %s AND state = 'IN_PROGRESS'""",
                                    (code, event_key),
                                )
                            except psycopg.Error:
                                # IN_PROGRESS is also a safe hold if the failure update cannot persist.
                                pass
                        raise
                    finally:
                        cursor.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (event_key,))
                        unlocked = cursor.fetchone()
                        if unlocked is None or unlocked[0] is not True:
                            raise QualificationLedgerUnavailable("qualification lock could not be released",
                                                                 external_call_started=external_call_started)
        except QualificationLedgerError:
            raise
        except psycopg.errors.UndefinedTable as exc:
            raise QualificationLedgerNotConfigured(
                "apply the lead-qualification ledger migration before enabling REAL qualification"
            ) from exc
        except (psycopg.Error, OSError) as exc:
            raise QualificationLedgerUnavailable("PostgreSQL qualification ledger is unavailable",
                                                 external_call_started=external_call_started) from exc
