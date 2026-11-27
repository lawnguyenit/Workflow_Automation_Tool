from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

import psycopg


_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_EVENT_TYPES = {
    "FOLLOWUP_ENROLLED",
    "REPLIED",
    "BOOKED",
    "OPTED_OUT",
    "MANUAL_PAUSE",
    "MANUAL_RESUME",
}
_TERMINAL_STATES = {"STOPPED_REPLIED", "STOPPED_BOOKED", "STOPPED_OPTED_OUT"}


class FollowupStateError(RuntimeError):
    code = "FOLLOWUP_STATE_UNAVAILABLE"


class FollowupStateNotConfigured(FollowupStateError):
    code = "FOLLOWUP_STATE_NOT_CONFIGURED"


class FollowupStateUnavailable(FollowupStateError):
    code = "FOLLOWUP_STATE_UNAVAILABLE"


class FollowupEventConflict(FollowupStateError):
    code = "SUPPRESSION_EVENT_CONFLICT"


class PostgresLeadFollowupState:
    """Persist an allow-list follow-up gate without storing source identifiers.

    Event and subject identities are keyed HMAC-SHA-256 digests produced by the module.
    Every state transition and its idempotency record commit in one transaction.
    """

    def __init__(self, *, dsn: str | None = None, connection_factory: Callable[..., Any] | None = None):
        self._dsn = dsn if dsn is not None else os.getenv("DATABASE_URL")
        self._connect = connection_factory or (
            lambda database_url: psycopg.connect(database_url, connect_timeout=5)
        )

    def check(self, *, subject_key: str) -> dict[str, Any]:
        self._require_digest(subject_key, "subject_key")
        dsn = self._require_dsn()
        try:
            with self._connect(dsn) as connection:
                with connection.cursor() as cursor:
                    self._set_transaction_timeouts(cursor)
                    cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (subject_key,))
                    cursor.execute(
                        "SELECT state, stopped_reason, pause_event_key FROM workflow_lead_followup_control "
                        "WHERE subject_key = %s",
                        (subject_key,),
                    )
                    row = cursor.fetchone()
            if row is None:
                return {"state": "NOT_ENROLLED", "can_send": False, "reason": "NOT_ENROLLED"}
            state, stopped_reason, _pause_event_key = row
            return {
                "state": state,
                "can_send": state == "ACTIVE",
                "reason": "ELIGIBLE" if state == "ACTIVE" else (stopped_reason or state),
            }
        except FollowupStateError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise FollowupStateUnavailable("could not read durable follow-up state") from exc

    def apply_event(
        self,
        *,
        subject_key: str,
        event_key: str,
        payload_fingerprint: str,
        event_type: str,
        expected_pause_key: str | None = None,
    ) -> dict[str, Any]:
        self._require_digest(subject_key, "subject_key")
        self._require_digest(event_key, "event_key")
        self._require_digest(payload_fingerprint, "payload_fingerprint")
        if event_type not in _EVENT_TYPES:
            raise FollowupStateNotConfigured("unsupported follow-up event type")
        if expected_pause_key is not None:
            self._require_digest(expected_pause_key, "expected_pause_key")
        if (event_type == "MANUAL_RESUME") != (expected_pause_key is not None):
            raise FollowupStateNotConfigured("manual resume must identify the pause event it intends to clear")
        dsn = self._require_dsn()

        try:
            with self._connect(dsn) as connection:
                with connection.cursor() as cursor:
                    self._set_transaction_timeouts(cursor)
                    # Lock the global event identity before the subject so concurrent
                    # reuse of one source event cannot race across different subjects.
                    cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (event_key,))
                    cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (subject_key,))
                    cursor.execute(
                        "SELECT subject_key, payload_fingerprint, event_type, outcome, state_after "
                        "FROM workflow_lead_followup_events WHERE event_key = %s",
                        (event_key,),
                    )
                    prior_event = cursor.fetchone()
                    if prior_event is not None:
                        old_subject, old_fingerprint, old_type, old_outcome, _old_state = prior_event
                        if old_subject != subject_key or old_fingerprint != payload_fingerprint or old_type != event_type:
                            raise FollowupEventConflict("event identity was reused with different suppression data")
                        current = self._read_state(cursor, subject_key)
                        return self._event_result(current, old_outcome, duplicate=True)

                    current = self._read_state(cursor, subject_key)
                    state, stopped_reason, pause_key, outcome = self._transition(
                        current=current,
                        event_key=event_key,
                        event_type=event_type,
                        expected_pause_key=expected_pause_key,
                    )
                    if state is not None:
                        cursor.execute(
                            """INSERT INTO workflow_lead_followup_control
                                   (subject_key, state, stopped_reason, pause_event_key)
                               VALUES (%s, %s, %s, %s)
                               ON CONFLICT (subject_key) DO UPDATE
                                 SET state = EXCLUDED.state,
                                     stopped_reason = EXCLUDED.stopped_reason,
                                     pause_event_key = EXCLUDED.pause_event_key,
                                     updated_at = now()""",
                            (subject_key, state, stopped_reason, pause_key),
                        )
                    state_after = state or "NOT_ENROLLED"
                    cursor.execute(
                        """INSERT INTO workflow_lead_followup_events
                               (event_key, subject_key, payload_fingerprint, event_type, outcome, state_after)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (event_key, subject_key, payload_fingerprint, event_type, outcome, state_after),
                    )
            return self._event_result((state_after, stopped_reason, pause_key), outcome, duplicate=False)
        except FollowupStateError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise FollowupStateUnavailable("could not commit durable follow-up event") from exc

    @staticmethod
    def _read_state(cursor: Any, subject_key: str) -> tuple[str, str | None, str | None] | None:
        cursor.execute(
            "SELECT state, stopped_reason, pause_event_key FROM workflow_lead_followup_control "
            "WHERE subject_key = %s",
            (subject_key,),
        )
        row = cursor.fetchone()
        return tuple(row) if row is not None else None

    @staticmethod
    def _transition(
        *,
        current: tuple[str, str | None, str | None] | None,
        event_key: str,
        event_type: str,
        expected_pause_key: str | None,
    ) -> tuple[str | None, str | None, str | None, str]:
        state, stopped_reason, pause_key = current if current is not None else (None, None, None)
        if state in _TERMINAL_STATES:
            return state, stopped_reason, pause_key, "ALREADY_TERMINAL"
        if event_type == "FOLLOWUP_ENROLLED":
            if state is None:
                return "ACTIVE", None, None, "ENROLLED"
            return state, stopped_reason, pause_key, "NO_STATE_CHANGE"
        if event_type in {"REPLIED", "BOOKED", "OPTED_OUT"}:
            terminal = {
                "REPLIED": ("STOPPED_REPLIED", "REPLIED"),
                "BOOKED": ("STOPPED_BOOKED", "BOOKED"),
                "OPTED_OUT": ("STOPPED_OPTED_OUT", "OPTED_OUT"),
            }[event_type]
            return terminal[0], terminal[1], None, terminal[1]
        if event_type == "MANUAL_PAUSE":
            if state == "PAUSED":
                return state, stopped_reason, event_key, "PAUSE_REPLACED"
            return "PAUSED", None, event_key, "PAUSED"
        if event_type == "MANUAL_RESUME":
            if state == "PAUSED" and pause_key == expected_pause_key:
                return "ACTIVE", None, None, "RESUMED"
            return state, stopped_reason, pause_key, "STALE_RESUME_REVIEW"
        raise FollowupStateNotConfigured("unsupported follow-up event type")

    @staticmethod
    def _event_result(
        current: tuple[str, str | None, str | None] | None,
        outcome: str,
        *,
        duplicate: bool,
    ) -> dict[str, Any]:
        state, stopped_reason, _pause_event_key = current if current is not None else ("NOT_ENROLLED", None, None)
        can_send = state == "ACTIVE"
        return {
            "state": state,
            "can_send": can_send,
            "reason": "ELIGIBLE" if can_send else (stopped_reason or state),
            "event_outcome": outcome,
            "duplicate": duplicate,
        }

    def _require_dsn(self) -> str:
        if not isinstance(self._dsn, str) or not self._dsn.strip():
            raise FollowupStateNotConfigured("DATABASE_URL is required for durable follow-up suppression")
        return self._dsn

    @staticmethod
    def _require_digest(value: str, name: str) -> None:
        if not isinstance(value, str) or not _DIGEST.fullmatch(value):
            raise FollowupStateNotConfigured(f"{name} must be a lowercase SHA-256 digest")

    @staticmethod
    def _set_transaction_timeouts(cursor: Any) -> None:
        cursor.execute("SET LOCAL lock_timeout = '3s'")
        cursor.execute("SET LOCAL statement_timeout = '10s'")
