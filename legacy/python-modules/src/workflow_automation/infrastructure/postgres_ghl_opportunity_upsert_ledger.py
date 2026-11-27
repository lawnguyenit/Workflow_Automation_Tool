from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from workflow_automation.contracts import ModuleResult, ResultState


_DIGEST = re.compile(r"^[a-f0-9]{64}$")


class GHLOpportunityLedgerError(RuntimeError):
    code = "GHL_OPPORTUNITY_LEDGER_UNAVAILABLE"


class GHLOpportunityLedgerNotConfigured(GHLOpportunityLedgerError):
    code = "GHL_OPPORTUNITY_LEDGER_NOT_CONFIGURED"


class GHLOpportunityAlreadyInProgress(GHLOpportunityLedgerError):
    code = "GHL_OPPORTUNITY_ALREADY_IN_PROGRESS"


class GHLOpportunityInputConflict(GHLOpportunityLedgerError):
    code = "GHL_OPPORTUNITY_INPUT_CONFLICT"


class GHLOpportunityReconciliationRequired(GHLOpportunityLedgerError):
    code = "GHL_OPPORTUNITY_RECONCILIATION_REQUIRED"


class PostgresGHLOpportunityUpsertLedger:
    """Cache confirmed writes and hold uncertain results for one source event."""

    def __init__(self, *, dsn: str | None = None, connection_factory: Callable[..., Any] | None = None):
        self._dsn = dsn if dsn is not None else os.getenv("DATABASE_URL")
        self._connect = connection_factory or psycopg.connect

    def run_once(
        self,
        *,
        event_key: str,
        input_fingerprint: str,
        operation: Callable[[], ModuleResult],
        uncertain_error_code: Callable[[ModuleResult], str | None],
    ) -> tuple[ModuleResult | dict[str, Any], bool]:
        if not isinstance(event_key, str) or not _DIGEST.fullmatch(event_key):
            raise GHLOpportunityLedgerNotConfigured("opportunity event key must be a SHA-256 digest")
        if not isinstance(input_fingerprint, str) or not _DIGEST.fullmatch(input_fingerprint):
            raise GHLOpportunityLedgerNotConfigured("opportunity input fingerprint must be a SHA-256 digest")
        if not isinstance(self._dsn, str) or not self._dsn.strip():
            raise GHLOpportunityLedgerNotConfigured("DATABASE_URL is required for REAL opportunity upserts")

        external_call_started = False
        try:
            with self._connect(self._dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (event_key,))
                    if not bool(cursor.fetchone()[0]):
                        raise GHLOpportunityAlreadyInProgress("another worker is processing this source event")
                    try:
                        cursor.execute(
                            """SELECT input_fingerprint, state, result
                               FROM workflow_ghl_opportunity_upserts WHERE event_key = %s""",
                            (event_key,),
                        )
                        existing = cursor.fetchone()
                        if existing is not None:
                            if existing[0] != input_fingerprint:
                                raise GHLOpportunityInputConflict(
                                    "source event was replayed with changed opportunity data"
                                )
                            if existing[1] == "SUCCEEDED":
                                saved = existing[2]
                                if (not isinstance(saved, dict)
                                        or set(saved) != {"opportunity_id", "new"}
                                        or not isinstance(saved.get("opportunity_id"), str)
                                        or not isinstance(saved.get("new"), bool)):
                                    raise GHLOpportunityLedgerError("stored opportunity result is invalid")
                                return {"state": "UPSERTED", **saved, "side_effect": False, "cached": True}, True
                            if existing[1] == "IN_PROGRESS":
                                raise GHLOpportunityAlreadyInProgress(
                                    "prior opportunity upsert has not been reconciled"
                                )
                            raise GHLOpportunityReconciliationRequired(
                                "prior opportunity upsert needs reconciliation"
                            )

                        cursor.execute(
                            """INSERT INTO workflow_ghl_opportunity_upserts
                                   (event_key, input_fingerprint, state)
                               VALUES (%s, %s, 'IN_PROGRESS')""",
                            (event_key, input_fingerprint),
                        )
                        external_call_started = True
                        result = operation()
                        uncertain = uncertain_error_code(result)
                        if uncertain:
                            cursor.execute(
                                """UPDATE workflow_ghl_opportunity_upserts
                                   SET state = 'UNKNOWN', error_code = %s, updated_at = now()
                                   WHERE event_key = %s""",
                                (uncertain, event_key),
                            )
                            return result, False

                        if result.status == ResultState.SUCCESS:
                            business = result.business_result
                            if (not isinstance(business, dict)
                                    or not isinstance(business.get("opportunity_id"), str)
                                    or not business["opportunity_id"]
                                    or not isinstance(business.get("new"), bool)):
                                cursor.execute(
                                    """UPDATE workflow_ghl_opportunity_upserts
                                       SET state = 'UNKNOWN', error_code = 'RESULT_UNVERIFIED', updated_at = now()
                                       WHERE event_key = %s""",
                                    (event_key,),
                                )
                                raise GHLOpportunityReconciliationRequired(
                                    "provider result cannot be safely cached"
                                )
                            saved = {"opportunity_id": business["opportunity_id"], "new": business["new"]}
                            cursor.execute(
                                """UPDATE workflow_ghl_opportunity_upserts
                                   SET state = 'SUCCEEDED', result = %s, error_code = NULL, updated_at = now()
                                   WHERE event_key = %s""",
                                (Jsonb(saved), event_key),
                            )
                        else:
                            cursor.execute("DELETE FROM workflow_ghl_opportunity_upserts WHERE event_key = %s",
                                           (event_key,))
                        return result, False
                    finally:
                        cursor.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (event_key,))
                        unlocked = cursor.fetchone()
                        if unlocked is None or unlocked[0] is not True:
                            raise GHLOpportunityLedgerError("opportunity event lock could not be released")
        except GHLOpportunityLedgerError:
            raise
        except psycopg.errors.UndefinedTable as exc:
            raise GHLOpportunityLedgerNotConfigured(
                "apply the GHL opportunity upsert ledger migration first"
            ) from exc
        except (psycopg.Error, OSError) as exc:
            if external_call_started:
                raise GHLOpportunityReconciliationRequired(
                    "opportunity write may have occurred; reconcile before retrying"
                ) from exc
            raise GHLOpportunityLedgerError("opportunity ledger is unavailable") from exc
