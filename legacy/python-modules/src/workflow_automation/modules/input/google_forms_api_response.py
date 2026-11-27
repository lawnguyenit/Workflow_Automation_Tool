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


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
_FIELD_LENGTHS = {
    "name": 250,
    "email": 320,
    "phone": 64,
    "service_requested": 500,
    "description": 10_000,
}
DEFAULT_FIELD_TITLES = {
    "name": "Tên đầy đủ",
    "email": "Email address",
    "phone": "Phone (optional)",
    "service_requested": "Service requested",
    "description": "Describe your request",
}


class GoogleFormsApiResponse:
    """Map one Google Forms API FormResponse using the matching read-only form schema.

    Authentication/polling belong to the caller (n8n). Response identity uses
    the immutable responseId scoped by the Form ID, not a mutable Sheet row.
    """

    module_id = "source.google_forms_api_response"

    def __init__(
        self,
        *,
        source_account: str = "portfolio-lead-form",
        field_titles: dict[str, str] | None = None,
    ) -> None:
        self.source_account = source_account
        self.field_titles = dict(field_titles or DEFAULT_FIELD_TITLES)

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        started = datetime.now(UTC)
        config_error = self._validate_configuration()
        if config_error:
            return self._failure(context, started, "SOURCE_NOT_CONFIGURED", config_error)
        if not isinstance(payload, dict) or set(payload) != {"form_id", "form", "response"}:
            return self._failure(context, started, "INVALID_TRIGGER_EVENT",
                                 "payload must contain exactly form_id, form, and response")

        form_id, form, response = payload["form_id"], payload["form"], payload["response"]
        if not isinstance(form_id, str) or not _ID_RE.fullmatch(form_id):
            return self._failure(context, started, "INVALID_FORM_ID", "form_id is invalid")
        if not isinstance(form, dict) or form.get("formId") != form_id:
            return self._failure(context, started, "FORM_SCHEMA_MISMATCH",
                                 "form schema must match the configured Form ID")
        if not isinstance(form.get("items"), list):
            return self._failure(context, started, "INVALID_FORM_SCHEMA",
                                 "form schema items must be a list")
        if not isinstance(response, dict):
            return self._failure(context, started, "INVALID_FORM_RESPONSE",
                                 "response must be an object")
        response_form_id = response.get("formId")
        if response_form_id is not None and response_form_id != form_id:
            return self._failure(context, started, "FORM_RESPONSE_MISMATCH",
                                 "response belongs to a different Form")
        response_id = response.get("responseId")
        if not isinstance(response_id, str) or not _ID_RE.fullmatch(response_id):
            return self._failure(context, started, "INVALID_RESPONSE_ID",
                                 "responseId must be a non-empty stable Form response identifier")
        answers = response.get("answers")
        if not isinstance(answers, dict):
            return self._failure(context, started, "INVALID_FORM_ANSWERS",
                                 "response answers must be keyed by question ID")

        question_ids, error = self._resolve_question_ids(form["items"])
        if error:
            return self._failure(context, started, "FORM_QUESTIONS_AMBIGUOUS", error)
        lead: dict[str, str] = {
            "source_system": "google_forms_api",
            "source_account": self.source_account,
            "source_event_id": f"{form_id}:{response_id}",
        }
        if len(lead["source_event_id"]) > 250:
            return self._failure(context, started, "SOURCE_EVENT_ID_TOO_LONG",
                                 "Form ID and responseId exceed the lead event key limit")

        for field, question_id in question_ids.items():
            answer = answers.get(question_id)
            value, answer_error = self._text_answer(answer)
            if answer_error:
                return self._failure(context, started, "UNSUPPORTED_FORM_ANSWER",
                                     f"The configured question for {field} must have one text answer")
            if len(value) > _FIELD_LENGTHS[field]:
                return self._failure(context, started, "FORM_ANSWER_TOO_LONG",
                                     f"The configured answer for {field} exceeds its field limit")
            lead[field] = value

        return ModuleResult(
            module_id=self.module_id,
            status=ResultState.SUCCESS,
            business_result={"lead": lead},
            metrics={"mapped_fields": len(self.field_titles)},
            context=context,
            started_at=started,
        ).complete()

    def _resolve_question_ids(self, items: list[Any]) -> tuple[dict[str, str], str | None]:
        title_to_ids: dict[str, list[str]] = {}
        for item in items:
            if not isinstance(item, dict):
                return {}, "form schema contains an invalid item"
            title = item.get("title")
            question_item = item.get("questionItem")
            if question_item is None:
                continue
            if not isinstance(question_item, dict):
                return {}, "form schema contains an invalid question item"
            question = question_item.get("question", {})
            if not isinstance(question, dict):
                return {}, "form schema contains an invalid question"
            question_id = question.get("questionId")
            if isinstance(title, str) and isinstance(question_id, str) and question_id:
                title_to_ids.setdefault(title, []).append(question_id)

        resolved: dict[str, str] = {}
        for field, title in self.field_titles.items():
            matches = title_to_ids.get(title, [])
            if len(matches) != 1:
                return {}, f"the configured title for {field} must match exactly one Form question"
            resolved[field] = matches[0]
        if len(set(resolved.values())) != len(resolved):
            return {}, "configured lead fields must map to distinct Form questions"
        return resolved, None

    @staticmethod
    def _text_answer(answer: Any) -> tuple[str, bool]:
        if answer is None:
            return "", False
        if not isinstance(answer, dict):
            return "", True
        text_answers = answer.get("textAnswers")
        if not isinstance(text_answers, dict):
            return "", True
        values = text_answers.get("answers", [])
        if not isinstance(values, list) or len(values) > 1:
            return "", True
        if not values:
            return "", False
        if not isinstance(values[0], dict):
            return "", True
        value = values[0].get("value", "")
        if value is None:
            return "", False
        return (value, False) if isinstance(value, str) else ("", True)

    def _validate_configuration(self) -> str | None:
        if not isinstance(self.source_account, str) or not _SCOPE_RE.fullmatch(self.source_account):
            return "source_account must be a stable non-sensitive scope identifier"
        if set(self.field_titles) != set(DEFAULT_FIELD_TITLES):
            return "field_titles must map exactly name, email, phone, service_requested, and description"
        if any(not isinstance(title, str) or not title for title in self.field_titles.values()):
            return "field_titles values must be non-empty exact Form question titles"
        if len(set(self.field_titles.values())) != len(self.field_titles):
            return "field_titles must identify distinct Form questions"
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
