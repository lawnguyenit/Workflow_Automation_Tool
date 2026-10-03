from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

from workflow_automation.contracts import (
    ExecutionContext,
    ModuleError,
    ModuleResult,
    ResultState,
    Retryability,
    RunMode,
)


_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_INVOICE_FIELDS = (
    "document_type",
    "sender",
    "invoice_number",
    "amount",
    "currency",
    "status",
    "confidence",
    "extraction_provider",
    "extraction_model",
)
_INSERT_SQL = """
    INSERT INTO workflow_invoices (
        source_system, source_account, source_message_id, source_attachment_id,
        invoice_data, payload_fingerprint, workflow_id, run_id, correlation_id, attempt
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (source_system, source_account, source_message_id, source_attachment_id)
    DO NOTHING
    RETURNING id
"""
_SELECT_SQL = """
    SELECT id, payload_fingerprint
    FROM workflow_invoices
    WHERE source_system = %s AND source_account = %s
      AND source_message_id = %s AND source_attachment_id = %s
"""
_ENQUEUE_NOTIFICATION_SQL = """
    INSERT INTO notification_outbox (idempotency_key, destination, payload, payload_fingerprint)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id, state, destination, payload_fingerprint
"""
_SELECT_NOTIFICATION_SQL = """
    SELECT id, state, destination, payload_fingerprint
    FROM notification_outbox WHERE idempotency_key = %s
"""
_UPDATE_NOTIFICATION_SQL = """
    UPDATE notification_outbox
    SET state = %s,
        error_code = %s,
        attempt_count = attempt_count + CASE WHEN %s = 'SENDING' THEN 1 ELSE 0 END,
        updated_at = now()
    WHERE id = %s
    RETURNING id, state, attempt_count
"""
_CLAIM_NOTIFICATION_SQL = """
    UPDATE notification_outbox
    SET state = 'SENDING', attempt_count = attempt_count + 1, updated_at = now()
    WHERE id = %s AND state = 'PENDING'
    RETURNING id, state, attempt_count
"""
_REDACT_NOTIFICATION_SQL = """
    UPDATE notification_outbox
    SET payload = '{}'::jsonb, updated_at = now()
    WHERE id = %s AND state = 'SENT'
    RETURNING id
"""
_NOTIFICATION_STATES = {
    "PENDING",
    "SENDING",
    "SENT",
    "FAILED",
    "UNKNOWN",
    "REVIEW_REQUIRED",
    "MOCKED",
    "PREVIEWED",
}
_ERROR_CODE_RE = re.compile(r"^[A-Z0-9_]{1,64}$")


class NotificationOutboxError(RuntimeError):
    """Safe outbox failure with a stable machine-readable code and retry hint."""

    def __init__(self, code: str, message: str, retryability: Retryability) -> None:
        super().__init__(message)
        self.code = code
        self.retryability = retryability


class _InputError(ValueError):
    pass


class PostgresStore:
    """Persist normalized invoice records with source-scoped, concurrency-safe dedupe.

    The composite source key is unique across all runs. An empty attachment ID means
    the email itself represents one document; callers with multiple documents per
    message must provide distinct attachment IDs. Conflicts use first-write-wins.
    """

    module_id = "storage.record"

    def __init__(
        self,
        connection_factory: Callable[..., Any] | None = None,
        source_account_scope: str | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._source_account_scope = source_account_scope.strip() if source_account_scope else None

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        started_at = datetime.now(UTC)
        warnings: list[str] = []
        try:
            records = self._validate_payload(payload, warnings)
        except _InputError as exc:
            return self._failure(context, started_at, "INVALID_INPUT", "validation", str(exc), Retryability.NOT_RETRYABLE)

        if context.mode in (RunMode.DRY_RUN, RunMode.REPLAY):
            preview_records = []
            for record, invoice_data, _fingerprint in records:
                public_record = {
                    field: record[field]
                    for field in (
                        "record_key",
                        "source_system",
                        "source_account",
                        "source_message_id",
                        "source_attachment_id",
                    )
                    if field in record
                }
                preview_records.append({
                    **public_record,
                    **invoice_data,
                    "record_id": None,
                    "duplicate": None,
                    "persistence_state": "PREVIEWED",
                })
            return ModuleResult(
                module_id=self.module_id,
                status=ResultState.SUCCESS,
                business_result={"records": preview_records},
                warnings=[*warnings, "Persistence was skipped in preview mode; duplicate status is unknown."],
                metrics={"record_count": len(preview_records), "preview_count": len(preview_records)},
                context=context,
                started_at=started_at,
            ).complete()

        database_url = os.environ.get("DATABASE_URL")
        if self._connection_factory is None and not database_url:
            return self._failure(
                context,
                started_at,
                "DATABASE_CONFIGURATION_MISSING",
                "configuration",
                "DATABASE_URL is required for PostgreSQL persistence.",
                Retryability.NOT_RETRYABLE,
            )

        connect = self._connection_factory or psycopg.connect
        connect_args = {"conninfo": database_url} if self._connection_factory is None else {}
        outputs: list[dict[str, Any]] = []
        duplicate_count = 0
        mismatch_count = 0
        try:
            with connect(**connect_args) as connection:
                with connection.cursor() as cursor:
                    for record, invoice_data, fingerprint in records:
                        key = (
                            record["source_system"],
                            record["source_account"],
                            record["source_message_id"],
                            record["source_attachment_id"],
                        )
                        cursor.execute(
                            _INSERT_SQL,
                            (
                                *key,
                                Jsonb(invoice_data),
                                fingerprint,
                                context.workflow_id,
                                context.run_id,
                                context.correlation_id,
                                context.attempt,
                            ),
                        )
                        inserted = cursor.fetchone()
                        if inserted is not None:
                            record_id = str(inserted[0])
                            duplicate = False
                            existing_fingerprint = fingerprint
                        else:
                            cursor.execute(_SELECT_SQL, key)
                            existing = cursor.fetchone()
                            if existing is None:
                                # Under READ COMMITTED, a conflicting insert is visible
                                # to the next statement after ON CONFLICT has waited.
                                raise RuntimeError("conflicting row could not be resolved")
                            record_id = str(existing[0])
                            existing_fingerprint = existing[1]
                            duplicate = True
                            duplicate_count += 1
                            if existing_fingerprint != fingerprint:
                                mismatch_count += 1
                        public_record = {
                            field: record[field]
                            for field in (
                                "record_key",
                                "source_system",
                                "source_account",
                                "source_message_id",
                                "source_attachment_id",
                            )
                            if field in record
                        }
                        outputs.append({
                            **public_record,
                            **invoice_data,
                            "record_id": record_id,
                            "duplicate": duplicate,
                        })
        except psycopg.errors.IntegrityError:
            return self._failure(
                context,
                started_at,
                "DATABASE_CONSTRAINT_VIOLATION",
                "database",
                "The database rejected an invoice record due to a constraint violation.",
                Retryability.NOT_RETRYABLE,
            )
        except (psycopg.OperationalError, psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected):
            return self._failure(
                context,
                started_at,
                "DATABASE_TEMPORARY_FAILURE",
                "database",
                "PostgreSQL could not complete the write. Retrying with the same source keys is safe.",
                Retryability.RETRYABLE,
            )
        except Exception:
            # Do not surface driver text: it can contain SQL, parameters, or connection data.
            return self._failure(
                context,
                started_at,
                "DATABASE_WRITE_OUTCOME_UNKNOWN",
                "database",
                "PostgreSQL did not confirm the write outcome. Replaying the same source keys is safe.",
                Retryability.UNKNOWN,
            )

        if mismatch_count:
            warnings.append(
                f"{mismatch_count} duplicate source record(s) had changed normalized fields; existing rows were preserved."
            )
        return ModuleResult(
            module_id=self.module_id,
            status=ResultState.SUCCESS,
            business_result={"records": outputs},
            warnings=warnings,
            metrics={
                "record_count": len(outputs),
                "inserted_count": len(outputs) - duplicate_count,
                "duplicate_count": duplicate_count,
                "duplicate_changed_count": mismatch_count,
            },
            context=context,
            started_at=started_at,
        ).complete()

    def enqueue_notification(
        self, key: str, destination: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Create or return one durable notification intent; does not send it."""
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Notification idempotency key must be a non-empty string.")
        if not isinstance(destination, str) or not destination.strip():
            raise ValueError("Notification destination must be a non-empty string.")
        if not isinstance(payload, dict):
            raise ValueError("Notification payload must be an object.")
        try:
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            raise ValueError("Notification payload must contain JSON-compatible values.") from None
        if len(serialized.encode("utf-8")) > 16_384:
            raise ValueError("Notification payload exceeds the 16 KiB limit.")
        fingerprint = hashlib.sha256(
            f"{destination.strip()}\n{serialized}".encode("utf-8")
        ).hexdigest()
        connection, connect_args = self._connect()
        try:
            with connection(**connect_args) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        _ENQUEUE_NOTIFICATION_SQL,
                        (key.strip(), destination.strip(), Jsonb(payload), fingerprint),
                    )
                    row = cursor.fetchone()
                    created = row is not None
                    if row is None:
                        cursor.execute(_SELECT_NOTIFICATION_SQL, (key.strip(),))
                        row = cursor.fetchone()
                    if row is None:
                        raise NotificationOutboxError(
                            "OUTBOX_WRITE_OUTCOME_UNKNOWN",
                            "PostgreSQL did not confirm the notification intent.",
                            Retryability.UNKNOWN,
                        )
                    elif row[2] != destination.strip() or row[3] != fingerprint:
                        raise NotificationOutboxError(
                            "OUTBOX_IDEMPOTENCY_KEY_REUSED",
                            "The notification key already belongs to a different destination or payload.",
                            Retryability.NOT_RETRYABLE,
                        )
                    return {"id": str(row[0]), "created": created, "state": row[1]}
        except NotificationOutboxError:
            raise
        except (psycopg.OperationalError, psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected):
            raise NotificationOutboxError(
                "OUTBOX_TEMPORARY_FAILURE",
                "PostgreSQL could not complete the notification intent write; retry with the same key.",
                Retryability.RETRYABLE,
            ) from None
        except psycopg.errors.IntegrityError:
            raise NotificationOutboxError(
                "OUTBOX_CONSTRAINT_VIOLATION",
                "The database rejected the notification intent due to a constraint violation.",
                Retryability.NOT_RETRYABLE,
            ) from None
        except Exception:
            raise NotificationOutboxError(
                "OUTBOX_WRITE_OUTCOME_UNKNOWN",
                "PostgreSQL did not confirm the notification intent; retry with the same key.",
                Retryability.UNKNOWN,
            ) from None

    def update_notification(
        self, notification_id: str | int, state: str, error_code: str | None = None
    ) -> dict[str, Any]:
        """Record delivery progress; SENT means a provider confirmed delivery only."""
        normalized_state = state.upper() if isinstance(state, str) else ""
        if normalized_state not in _NOTIFICATION_STATES:
            raise ValueError("Notification state is unsupported.")
        if error_code is not None and (
            not isinstance(error_code, str) or not _ERROR_CODE_RE.fullmatch(error_code)
        ):
            raise ValueError("error_code must be a short uppercase diagnostic code.")
        try:
            numeric_id = int(notification_id)
            if numeric_id < 1:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("notification_id must be a positive database ID.") from None

        connection, connect_args = self._connect()
        try:
            with connection(**connect_args) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        _UPDATE_NOTIFICATION_SQL,
                        (normalized_state, error_code, normalized_state, numeric_id),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise NotificationOutboxError(
                            "OUTBOX_RECORD_NOT_FOUND",
                            "The notification intent does not exist.",
                            Retryability.NOT_RETRYABLE,
                        )
                    return {"id": str(row[0]), "state": row[1], "attempt_count": row[2]}
        except NotificationOutboxError:
            raise
        except (psycopg.OperationalError, psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected):
            raise NotificationOutboxError(
                "OUTBOX_TEMPORARY_FAILURE",
                "PostgreSQL could not update notification progress.",
                Retryability.RETRYABLE,
            ) from None
        except Exception:
            raise NotificationOutboxError(
                "OUTBOX_UPDATE_OUTCOME_UNKNOWN",
                "PostgreSQL did not confirm the notification progress update.",
                Retryability.UNKNOWN,
            ) from None

    def claim_notification(self, notification_id: str | int) -> bool:
        """Atomically move one pending intent to SENDING; only one worker can claim it."""
        try:
            numeric_id = int(notification_id)
            if numeric_id < 1:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("notification_id must be a positive database ID.") from None
        connection, connect_args = self._connect()
        try:
            with connection(**connect_args) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(_CLAIM_NOTIFICATION_SQL, (numeric_id,))
                    return cursor.fetchone() is not None
        except (psycopg.OperationalError, psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected):
            raise NotificationOutboxError(
                "OUTBOX_TEMPORARY_FAILURE",
                "PostgreSQL could not claim the notification intent.",
                Retryability.RETRYABLE,
            ) from None
        except Exception:
            raise NotificationOutboxError(
                "OUTBOX_CLAIM_OUTCOME_UNKNOWN",
                "PostgreSQL did not confirm the notification claim.",
                Retryability.UNKNOWN,
            ) from None

    def redact_sent_notification(self, notification_id: str | int) -> bool:
        """Clear stored recipient/body after confirmed delivery, retaining the fingerprint."""
        try:
            numeric_id = int(notification_id)
            if numeric_id < 1:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("notification_id must be a positive database ID.") from None
        connection, connect_args = self._connect()
        try:
            with connection(**connect_args) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(_REDACT_NOTIFICATION_SQL, (numeric_id,))
                    return cursor.fetchone() is not None
        except (psycopg.OperationalError, psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected):
            raise NotificationOutboxError(
                "OUTBOX_TEMPORARY_FAILURE",
                "PostgreSQL could not redact the delivered notification payload.",
                Retryability.RETRYABLE,
            ) from None
        except Exception:
            raise NotificationOutboxError(
                "OUTBOX_REDACTION_OUTCOME_UNKNOWN",
                "PostgreSQL did not confirm notification payload redaction.",
                Retryability.UNKNOWN,
            ) from None

    def _connect(self) -> tuple[Callable[..., Any], dict[str, Any]]:
        if self._connection_factory is not None:
            return self._connection_factory, {}
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise NotificationOutboxError(
                "DATABASE_CONFIGURATION_MISSING",
                "DATABASE_URL is required for PostgreSQL persistence.",
                Retryability.NOT_RETRYABLE,
            )
        return psycopg.connect, {"conninfo": database_url}

    def _validate_payload(
        self,
        payload: dict[str, Any], warnings: list[str]
    ) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise _InputError("Payload must include a records list.")
        if not payload["records"]:
            raise _InputError("records must contain at least one invoice record.")

        validated = []
        for index, raw in enumerate(payload["records"]):
            if not isinstance(raw, dict):
                raise _InputError(f"records[{index}] must be an object.")
            source_account = raw.get("source_account") or self._source_account_scope
            source_fields = {
                "source_system": raw.get("source_system"),
                "source_account": source_account,
                "source_message_id": raw.get("source_message_id"),
            }
            if any(
                not isinstance(value, str) or not value.strip()
                for value in source_fields.values()
            ):
                raise _InputError(
                    f"records[{index}] requires non-empty source_system, source_message_id, and source_account or configured scope."
                )
            attachment_id = raw.get("source_attachment_id")
            if attachment_id is None or attachment_id == "":
                attachment_id = ""
                warnings.append(
                    f"records[{index}] has no attachment ID; the source email is treated as its single document."
                )
            if not isinstance(attachment_id, str):
                raise _InputError(f"records[{index}].source_attachment_id must be a string or null.")

            amount_raw = raw.get("amount")
            try:
                amount = Decimal(str(amount_raw))
                if not amount.is_finite():
                    raise InvalidOperation
            except (InvalidOperation, ValueError, TypeError):
                raise _InputError(f"records[{index}].amount must be a finite decimal number.") from None
            if not isinstance(raw.get("currency"), str) or not _CURRENCY_RE.fullmatch(raw["currency"]):
                raise _InputError(f"records[{index}].currency must be a three-letter uppercase currency code.")

            normalized = {
                field: raw.get(field)
                for field in _INVOICE_FIELDS
                if field in raw
            }
            normalized["amount"] = str(amount)
            if not normalized.get("invoice_number"):
                raise _InputError(f"records[{index}].invoice_number is required.")
            if not normalized.get("document_type"):
                normalized["document_type"] = "invoice"
            if not normalized.get("status"):
                normalized["status"] = "EXTRACTED"
            if normalized.get("confidence") is not None:
                try:
                    confidence = float(normalized["confidence"])
                except (ValueError, TypeError):
                    raise _InputError(f"records[{index}].confidence must be between 0 and 1.") from None
                if not 0 <= confidence <= 1:
                    raise _InputError(f"records[{index}].confidence must be between 0 and 1.")
                normalized["confidence"] = confidence

            key_fields = {
                "source_system": source_fields["source_system"].strip(),
                "source_account": source_fields["source_account"].strip(),
                "source_message_id": source_fields["source_message_id"].strip(),
                "source_attachment_id": attachment_id,
            }
            fingerprint = hashlib.sha256(
                json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            validated.append(({**raw, **key_fields}, normalized, fingerprint))
        return validated

    def _failure(
        self,
        context: ExecutionContext,
        started_at: datetime,
        code: str,
        category: str,
        message: str,
        retryability: Retryability,
    ) -> ModuleResult:
        return ModuleResult(
            module_id=self.module_id,
            status=ResultState.FAILURE,
            errors=[
                ModuleError(
                    code=code,
                    category=category,
                    message=message,
                    retryability=retryability,
                )
            ],
            context=context,
            started_at=started_at,
        ).complete()
