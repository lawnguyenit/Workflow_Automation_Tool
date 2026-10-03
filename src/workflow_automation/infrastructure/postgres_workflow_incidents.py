from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_INCIDENT_STATUSES = {"PARTIAL", "FAILURE", "REVIEW_REQUIRED"}
_RESOLUTION_CODES = {
    "RETRY_COMPLETED", "PROVIDER_STATE_CONFIRMED", "MANUAL_REPAIR", "DUPLICATE_CONFIRMED", "DISMISSED"
}


class WorkflowIncidentLogError(RuntimeError):
    """A sanitized, stable error from workflow incident recording or resolution."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PostgresWorkflowIncidentLog:
    """Persist bounded workflow failure metadata without storing run payloads or error text.

    Incidents are append-once per workflow/run/attempt. Resolving one acknowledges
    operator action; it does not retry a workflow or prove an external side effect.
    """

    def __init__(self, *, dsn: str | None = None,
                 connection_factory: Callable[..., Any] | None = None) -> None:
        self._dsn = dsn if dsn is not None else os.getenv("DATABASE_URL")
        self._connect = connection_factory or psycopg.connect

    def record_result(self, *, workflow_id: str, run_id: str, attempt: int,
                      mode: str, result: dict[str, Any]) -> dict[str, Any] | None:
        normalized_mode = mode.upper() if isinstance(mode, str) else ""
        if normalized_mode not in {"MOCK", "REAL"}:
            return None
        if not isinstance(workflow_id, str) or not _SAFE_ID.fullmatch(workflow_id):
            raise WorkflowIncidentLogError("INVALID_WORKFLOW_ID")
        if not isinstance(run_id, str) or not run_id or len(run_id) > 200:
            raise WorkflowIncidentLogError("INVALID_RUN_ID")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise WorkflowIncidentLogError("INVALID_ATTEMPT")
        if not isinstance(result, dict):
            raise WorkflowIncidentLogError("INVALID_RESULT")

        status = result.get("status")
        if not isinstance(status, str) or status not in _INCIDENT_STATUSES:
            return None
        diagnostics = self._sanitize_steps(result.get("steps", {}))
        serialized = json.dumps(diagnostics, sort_keys=True, separators=(",", ":"), allow_nan=False)
        fingerprint = hashlib.sha256(f"{normalized_mode}\n{status}\n{serialized}".encode()).hexdigest()
        event_key = hashlib.sha256(
            f"workflow-incident:v1\0{workflow_id}\0{run_id}\0{attempt}".encode()
        ).hexdigest()
        if not isinstance(self._dsn, str) or not self._dsn.strip():
            raise WorkflowIncidentLogError("DATABASE_NOT_CONFIGURED")

        try:
            with self._connect(self._dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO workflow_run_incidents
                               (event_key, workflow_id, attempt, mode, status, diagnostics, diagnostic_fingerprint)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (event_key) DO NOTHING
                           RETURNING incident_id, resolved_at""",
                        (event_key, workflow_id, attempt, normalized_mode, status,
                         Jsonb(diagnostics), fingerprint),
                    )
                    row = cursor.fetchone()
                    duplicate = row is None
                    if row is None:
                        cursor.execute(
                            """SELECT incident_id, diagnostic_fingerprint, resolved_at
                               FROM workflow_run_incidents WHERE event_key = %s""",
                            (event_key,),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            raise WorkflowIncidentLogError("INCIDENT_WRITE_OUTCOME_UNKNOWN")
                        if row[1] != fingerprint:
                            raise WorkflowIncidentLogError("INCIDENT_EVENT_CONFLICT")
                        incident_id, resolved_at = row[0], row[2]
                    else:
                        incident_id, resolved_at = row
                    return {
                        "incident_id": str(incident_id),
                        "status": status,
                        "duplicate": duplicate,
                        "resolved": resolved_at is not None,
                    }
        except WorkflowIncidentLogError:
            raise
        except (psycopg.Error, OSError):
            raise WorkflowIncidentLogError("INCIDENT_STORE_UNAVAILABLE") from None

    def list_incidents(self, *, include_resolved: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise WorkflowIncidentLogError("INVALID_LIMIT")
        if not isinstance(self._dsn, str) or not self._dsn.strip():
            raise WorkflowIncidentLogError("DATABASE_NOT_CONFIGURED")
        try:
            with self._connect(self._dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT incident_id, workflow_id, attempt, mode, status, diagnostics,
                                  created_at, resolved_at, resolution_code
                           FROM workflow_run_incidents
                           WHERE (%s OR resolved_at IS NULL)
                           ORDER BY created_at DESC, incident_id DESC
                           LIMIT %s""",
                        (include_resolved, limit),
                    )
                    return [
                        {
                            "incident_id": str(row[0]), "workflow_id": row[1], "attempt": row[2],
                            "mode": row[3], "status": row[4], "diagnostics": row[5],
                            "created_at": row[6].isoformat(),
                            "resolved_at": row[7].isoformat() if row[7] else None,
                            "resolution_code": row[8],
                        }
                        for row in cursor.fetchall()
                    ]
        except WorkflowIncidentLogError:
            raise
        except (psycopg.Error, OSError):
            raise WorkflowIncidentLogError("INCIDENT_STORE_UNAVAILABLE") from None

    def get_incident(self, *, incident_id: str) -> dict[str, Any] | None:
        try:
            normalized_id = str(UUID(incident_id))
        except (ValueError, TypeError, AttributeError):
            raise WorkflowIncidentLogError("INVALID_INCIDENT_ID") from None
        if not isinstance(self._dsn, str) or not self._dsn.strip():
            raise WorkflowIncidentLogError("DATABASE_NOT_CONFIGURED")
        try:
            with self._connect(self._dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT incident_id, workflow_id, attempt, mode, status, diagnostics,
                                  created_at, resolved_at, resolution_code
                           FROM workflow_run_incidents WHERE incident_id = %s""",
                        (normalized_id,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        return None
                    return {
                        "incident_id": str(row[0]), "workflow_id": row[1], "attempt": row[2],
                        "mode": row[3], "status": row[4], "diagnostics": row[5],
                        "created_at": row[6].isoformat(),
                        "resolved_at": row[7].isoformat() if row[7] else None,
                        "resolution_code": row[8],
                    }
        except WorkflowIncidentLogError:
            raise
        except (psycopg.Error, OSError):
            raise WorkflowIncidentLogError("INCIDENT_STORE_UNAVAILABLE") from None

    def resolve(self, *, incident_id: str, resolution_code: str) -> dict[str, Any]:
        try:
            normalized_id = str(UUID(incident_id))
        except (ValueError, TypeError, AttributeError):
            raise WorkflowIncidentLogError("INVALID_INCIDENT_ID") from None
        normalized_code = resolution_code.upper() if isinstance(resolution_code, str) else ""
        if normalized_code not in _RESOLUTION_CODES:
            raise WorkflowIncidentLogError("INVALID_RESOLUTION_CODE")
        if not isinstance(self._dsn, str) or not self._dsn.strip():
            raise WorkflowIncidentLogError("DATABASE_NOT_CONFIGURED")
        try:
            with self._connect(self._dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """UPDATE workflow_run_incidents
                           SET resolved_at = COALESCE(resolved_at, now()),
                               resolution_code = COALESCE(resolution_code, %s)
                           WHERE incident_id = %s
                           RETURNING incident_id, resolved_at, resolution_code""",
                        (normalized_code, normalized_id),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        return {"found": False, "resolved": False}
                    return {
                        "found": True, "incident_id": str(row[0]),
                        "resolved_at": row[1].isoformat(), "resolution_code": row[2],
                    }
        except (psycopg.Error, OSError):
            raise WorkflowIncidentLogError("INCIDENT_STORE_UNAVAILABLE") from None

    @staticmethod
    def _sanitize_steps(steps: Any) -> dict[str, Any]:
        if not isinstance(steps, dict) or len(steps) > 50:
            raise WorkflowIncidentLogError("INVALID_STEP_DIAGNOSTICS")
        safe_steps: dict[str, Any] = {}
        for raw_step_id, raw_step in steps.items():
            step_id = raw_step_id if isinstance(raw_step_id, str) and _SAFE_ID.fullmatch(raw_step_id) else "unknown"
            if not isinstance(raw_step, dict):
                continue
            raw_status = raw_step.get("status")
            step_status = (raw_status if isinstance(raw_status, str)
                           and raw_status in _INCIDENT_STATUSES | {"SUCCESS"} else "UNKNOWN")
            raw_module_id = raw_step.get("module_id")
            module_id = raw_module_id if isinstance(raw_module_id, str) and _SAFE_ID.fullmatch(raw_module_id) else "unknown"
            raw_errors = raw_step.get("errors", [])
            if not isinstance(raw_errors, list):
                raw_errors = []
            errors = []
            for raw_error in raw_errors[:8]:
                if not isinstance(raw_error, dict):
                    continue
                code = raw_error.get("code")
                category = raw_error.get("category")
                retryability = raw_error.get("retryability")
                errors.append({
                    "code": code if isinstance(code, str) and _SAFE_ID.fullmatch(code) else "UNKNOWN_ERROR",
                    "category": category if isinstance(category, str) and _SAFE_ID.fullmatch(category) else "UNKNOWN",
                    "retryability": (retryability if isinstance(retryability, str)
                                     and retryability in {"RETRYABLE", "NOT_RETRYABLE", "UNKNOWN"}
                                     else "UNKNOWN"),
                })
            safe_steps[step_id] = {"module_id": module_id, "status": step_status, "errors": errors}
        return safe_steps
