from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from workflow_automation.contracts import (
    ExecutionContext,
    ModuleError,
    ModuleResult,
    ResultState,
    Retryability,
)


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
DEFAULT_HEADER_MAP = {
    "name": "Tên đầy đủ",
    "email": "Email address",
    "phone": "Phone (optional)",
    "service_requested": "Service requested",
    "description": "Describe your request",
}


class GoogleFormResponseRow:
    """Map one n8n Google Sheets Trigger row to the lead intake contract.

    Google authentication and polling belong to n8n's native trigger. This
    module is deliberately a pure mapper: it makes no Google API calls and
    relies on the trigger's row_number for source-event idempotency; stability
    depends on the response tab remaining append-only and unreordered.
    """

    module_id = "source.google_form_response"

    def __init__(
        self,
        *,
        spreadsheet_id: str | None,
        sheet_id: int | str | None,
        source_account: str = "portfolio-lead-form",
        header_map: dict[str, str] | None = None,
    ) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.sheet_id = int(sheet_id) if isinstance(sheet_id, str) and sheet_id.isdigit() else sheet_id
        self.source_account = source_account
        self.header_map = dict(header_map or DEFAULT_HEADER_MAP)

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        started = datetime.now(UTC)
        config_error = self._validate_configuration()
        if config_error:
            return self._failure(context, started, "SOURCE_NOT_CONFIGURED", config_error)
        if not isinstance(payload, dict) or set(payload) != {"row_number", "row", "change_type"}:
            return self._failure(context, started, "INVALID_TRIGGER_EVENT",
                                 "payload must contain exactly row_number, row, and change_type")
        row_number = payload["row_number"]
        row = payload["row"]
        change_type = payload["change_type"]
        if change_type != "added":
            return self._failure(context, started, "UNSUPPORTED_CHANGE_TYPE",
                                 "only newly added response rows may be processed")
        if isinstance(row_number, bool) or not isinstance(row_number, int) or row_number < 2:
            return self._failure(context, started, "INVALID_ROW_NUMBER",
                                 "row_number must be an integer of at least 2")
        if not isinstance(row, dict) or not all(isinstance(k, str) for k in row):
            return self._failure(context, started, "INVALID_ROW",
                                 "row must be an object keyed by the Google Sheets header text")
        if any(header not in row for header in self.header_map.values()):
            return self._failure(context, started, "FORM_HEADERS_CHANGED",
                                 "A configured source header is missing; update the instance mapping before processing")

        lead: dict[str, str] = {
            "source_system": "google_forms",
            "source_account": self.source_account,
            "source_event_id": f"{self.spreadsheet_id}:{self.sheet_id}:row:{row_number}",
        }
        for target, header in self.header_map.items():
            value = row[header]
            if value is None:
                value = ""
            if not isinstance(value, str):
                return self._failure(context, started, "INVALID_ROW_VALUE",
                                     f"The configured source column for {target} must contain text")
            lead[target] = value

        return ModuleResult(
            module_id=self.module_id,
            status=ResultState.SUCCESS,
            business_result={"lead": lead},
            metrics={"row_number": row_number, "mapped_fields": len(self.header_map)},
            context=context,
            started_at=started,
        ).complete()

    def _validate_configuration(self) -> str | None:
        if not isinstance(self.spreadsheet_id, str) or not _ID_RE.fullmatch(self.spreadsheet_id):
            return "spreadsheet_id must be configured with a Google Sheets ID"
        sheet_id = self.sheet_id
        if isinstance(sheet_id, bool) or not isinstance(sheet_id, int) or sheet_id < 0:
            return "sheet_id must be configured with the numeric Google Sheets tab ID"
        if not isinstance(self.source_account, str) or not _SCOPE_RE.fullmatch(self.source_account):
            return "source_account must be a stable non-sensitive scope identifier"
        if set(self.header_map) != set(DEFAULT_HEADER_MAP):
            return "header_map must map exactly name, email, phone, service_requested, and description"
        if any(not isinstance(value, str) or not value.strip() for value in self.header_map.values()):
            return "header_map values must be non-empty exact spreadsheet header strings"
        if len(set(self.header_map.values())) != len(self.header_map):
            return "header_map values must identify distinct source columns"
        return None

    def _failure(self, context: ExecutionContext, started: datetime,
                 code: str, message: str) -> ModuleResult:
        return ModuleResult(
            module_id=self.module_id,
            status=ResultState.FAILURE,
            errors=[ModuleError(code=code, category="VALIDATION", message=message,
                                retryability=Retryability.NOT_RETRYABLE)],
            context=context,
            started_at=started,
        ).complete()
