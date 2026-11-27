from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from workflow_automation.contracts import (
    ExecutionContext,
    ModuleError,
    ModuleResult,
    ResultState,
    Retryability,
)


_INVOICE_FIELDS = ("document_type", "sender", "invoice_number", "amount", "currency")
_ALLOWED_FIELDS = {
    "record_key", *_INVOICE_FIELDS, "source_ids", "filename", "extraction_provider",
    "extraction_model", "source_system", "source_account", "source_message_id",
    "source_attachment_id", "status", "confidence", "missing_fields", "conflicts", "error_code",
}


class Validate:
    """Validate extracted records using explicit, configurable business thresholds."""

    module_id = "data.validate"

    def __init__(
        self,
        *,
        required_fields: tuple[str, ...] | list[str] = _INVOICE_FIELDS,
        allowed_currencies: set[str] | list[str] | tuple[str, ...] | None = None,
        minimum_amount: Decimal | int | float | None = 0,
        maximum_amount: Decimal | int | float | None = None,
        minimum_confidence: float | None = None,
        allowed_fields: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> None:
        unknown = set(required_fields) - set(_INVOICE_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported required fields: {', '.join(sorted(unknown))}")
        self.required_fields = tuple(dict.fromkeys(required_fields))
        self.allowed_currencies = None if allowed_currencies is None else {value.upper() for value in allowed_currencies}
        self.minimum_amount = _decimal_threshold(minimum_amount, "minimum_amount")
        self.maximum_amount = _decimal_threshold(maximum_amount, "maximum_amount")
        if self.minimum_amount is not None and self.maximum_amount is not None and self.minimum_amount > self.maximum_amount:
            raise ValueError("minimum_amount cannot exceed maximum_amount")
        if minimum_confidence is not None and not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0 and 1")
        self.minimum_confidence = minimum_confidence
        self.allowed_fields = set(_ALLOWED_FIELDS if allowed_fields is None else allowed_fields)
        if "record_key" not in self.allowed_fields:
            raise ValueError("allowed_fields must include record_key")

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        records = payload.get("records")
        if not isinstance(records, list):
            return ModuleResult(
                module_id=self.module_id,
                status=ResultState.FAILURE,
                errors=[self._module_error("INVALID_RECORDS", "Input must contain a records list")],
                context=context,
            ).complete()

        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        module_errors: list[ModuleError] = []
        seen_keys: set[str] = set()

        for index, record in enumerate(records):
            if not isinstance(record, dict):
                item_errors = [{"code": "INVALID_RECORD", "field": None}]
                rejected.append({"record_key": None, "source_ids": [], "errors": item_errors, "record": record})
                module_errors.append(self._module_error("INVALID_RECORD", f"Record at index {index} must be an object"))
                continue

            key = record.get("record_key")
            if not isinstance(key, str) or not key.strip():
                item_errors = [{"code": "MISSING_RECORD_KEY", "field": "record_key"}]
            elif key in seen_keys:
                item_errors = [{"code": "DUPLICATE_RECORD_KEY", "field": "record_key"}]
            else:
                seen_keys.add(key)
                item_errors = self._validate_record(record)

            if item_errors:
                rejected.append({
                    "record_key": key if isinstance(key, str) else None,
                    "source_ids": _source_ids(record.get("source_ids")),
                    "errors": item_errors,
                    "record": record,
                })
                module_errors.extend(
                    self._module_error(
                        item["code"],
                        f"Record {key!r} failed validation ({item['code']})",
                    )
                    for item in item_errors
                )
            else:
                accepted.append(self._normalize(record))

        if rejected and accepted:
            status = ResultState.PARTIAL
        elif rejected:
            status = ResultState.REVIEW_REQUIRED
        else:
            status = ResultState.SUCCESS
        return ModuleResult(
            module_id=self.module_id,
            status=status,
            business_result={"records": accepted, "rejected": rejected},
            errors=module_errors,
            context=context,
        ).complete()

    def _validate_record(self, record: dict[str, Any]) -> list[dict[str, str | None]]:
        issues: list[dict[str, str | None]] = []
        for field in record:
            if field not in self.allowed_fields:
                issues.append({"code": "UNSUPPORTED_FIELD", "field": field})

        status = record.get("status")
        if status != "EXTRACTED":
            issues.append({"code": "EXTRACTION_REQUIRES_REVIEW", "field": "status"})

        for field in self.required_fields:
            value = record.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                issues.append({"code": f"MISSING_{field.upper()}", "field": field})

        for field in ("document_type", "sender", "invoice_number"):
            value = record.get(field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                issues.append({"code": f"INVALID_{field.upper()}", "field": field})

        amount = record.get("amount")
        if amount is not None:
            parsed_amount = _as_decimal(amount)
            if parsed_amount is None:
                issues.append({"code": "INVALID_AMOUNT", "field": "amount"})
            else:
                if self.minimum_amount is not None and parsed_amount < self.minimum_amount:
                    issues.append({"code": "AMOUNT_BELOW_MINIMUM", "field": "amount"})
                if self.maximum_amount is not None and parsed_amount > self.maximum_amount:
                    issues.append({"code": "AMOUNT_ABOVE_MAXIMUM", "field": "amount"})

        currency = record.get("currency")
        if currency is not None:
            if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3}", currency.strip()):
                issues.append({"code": "INVALID_CURRENCY", "field": "currency"})
            elif self.allowed_currencies is not None and currency.strip().upper() not in self.allowed_currencies:
                issues.append({"code": "UNSUPPORTED_CURRENCY", "field": "currency"})

        confidence = record.get("confidence")
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                issues.append({"code": "INVALID_CONFIDENCE", "field": "confidence"})
            elif self.minimum_confidence is not None and confidence < self.minimum_confidence:
                issues.append({"code": "CONFIDENCE_BELOW_MINIMUM", "field": "confidence"})
        elif self.minimum_confidence is not None:
            issues.append({"code": "MISSING_CONFIDENCE", "field": "confidence"})

        source_ids = record.get("source_ids")
        if source_ids is not None and (not isinstance(source_ids, list) or any(not isinstance(item, str) for item in source_ids)):
            issues.append({"code": "INVALID_SOURCE_IDS", "field": "source_ids"})
        return _unique_issues(issues)

    @staticmethod
    def _normalize(record: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(record)
        if isinstance(normalized.get("currency"), str):
            normalized["currency"] = normalized["currency"].strip().upper()
        if isinstance(normalized.get("amount"), Decimal):
            normalized["amount"] = float(normalized["amount"])
        return normalized

    @staticmethod
    def _module_error(code: str, message: str) -> ModuleError:
        return ModuleError(
            code=code,
            category="VALIDATION",
            message=message,
            retryability=Retryability.NOT_RETRYABLE,
        )


def _decimal_threshold(value: Decimal | int | float | None, name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be a finite number") from None
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite number")
    return result


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _source_ids(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _unique_issues(issues: list[dict[str, str | None]]) -> list[dict[str, str | None]]:
    seen: set[tuple[str, str | None]] = set()
    unique = []
    for issue in issues:
        key = (issue["code"], issue["field"])
        if key not in seen:
            unique.append(issue)
            seen.add(key)
    return unique
