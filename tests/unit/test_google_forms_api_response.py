from copy import deepcopy

from workflow_automation.contracts import ExecutionContext
from workflow_automation.modules.input.google_forms_api_response import (
    DEFAULT_FIELD_TITLES,
    GoogleFormsApiResponse,
)


FORM_ID = "syntheticGoogleForm123"
RESPONSE_ID = "syntheticResponse456"


def _form():
    return {
        "formId": FORM_ID,
        "items": [
            {"title": title,
             "questionItem": {"question": {"questionId": f"question-{field}"}}}
            for field, title in DEFAULT_FIELD_TITLES.items()
        ],
    }


def _response():
    return {
        "responseId": RESPONSE_ID,
        "createTime": "2026-10-03T03:00:00Z",
        "lastSubmittedTime": "2026-10-03T03:00:00Z",
        "answers": {
            f"question-{field}": {"textAnswers": {"answers": [{"value": value}]}}
            for field, value in {
                "name": "Taylor Example",
                "email": "taylor@example.invalid",
                "phone": "+15550102030",
                "service_requested": "Workflow automation",
                "description": "Synthetic Forms API response.",
            }.items()
        },
    }


def _run(form=None, response=None):
    return GoogleFormsApiResponse().execute(
        {"form_id": FORM_ID, "form": form or _form(), "response": response or _response()},
        ExecutionContext(workflow_id="test"),
    )


def test_maps_google_forms_api_response_by_question_id_and_uses_stable_response_id():
    result = _run()
    assert result.status == "SUCCESS"
    assert result.business_result["lead"] == {
        "source_system": "google_forms_api",
        "source_account": "portfolio-lead-form",
        "source_event_id": f"{FORM_ID}:{RESPONSE_ID}",
        "name": "Taylor Example",
        "email": "taylor@example.invalid",
        "phone": "+15550102030",
        "service_requested": "Workflow automation",
        "description": "Synthetic Forms API response.",
    }


def test_missing_answers_remain_empty_and_do_not_get_invented():
    response = _response()
    del response["answers"]["question-email"]
    result = _run(response=response)
    assert result.status == "SUCCESS"
    assert result.business_result["lead"]["email"] == ""


def test_form_schema_and_response_identity_must_match():
    wrong_form = _form()
    wrong_form["formId"] = "differentForm123"
    result = _run(form=wrong_form)
    assert result.errors[0].code == "FORM_SCHEMA_MISMATCH"

    response = _response()
    response["formId"] = "differentForm123"
    result = _run(response=response)
    assert result.errors[0].code == "FORM_RESPONSE_MISMATCH"


def test_question_title_drift_or_duplicate_question_fails_closed():
    form = _form()
    form["items"][0]["title"] = "Changed question"
    result = _run(form=form)
    assert result.errors[0].code == "FORM_QUESTIONS_AMBIGUOUS"

    form = _form()
    form["items"].append(deepcopy(form["items"][0]))
    result = _run(form=form)
    assert result.errors[0].code == "FORM_QUESTIONS_AMBIGUOUS"


def test_multiple_text_answers_and_non_text_values_are_rejected_without_echoing_pii():
    response = _response()
    response["answers"]["question-name"]["textAnswers"]["answers"].append({"value": "Another"})
    result = _run(response=response)
    assert result.errors[0].code == "UNSUPPORTED_FORM_ANSWER"
    assert "Taylor Example" not in str(result.model_dump())

    response = _response()
    response["answers"]["question-email"] = {"fileUploadAnswers": {"answers": []}}
    result = _run(response=response)
    assert result.errors[0].code == "UNSUPPORTED_FORM_ANSWER"


def test_oversized_answer_and_malformed_form_schema_fail_closed():
    response = _response()
    response["answers"]["question-description"]["textAnswers"]["answers"][0]["value"] = "x" * 10_001
    result = _run(response=response)
    assert result.errors[0].code == "FORM_ANSWER_TOO_LONG"

    form = _form()
    form["items"][0]["questionItem"] = None
    result = _run(form=form)
    assert result.errors[0].code == "FORM_QUESTIONS_AMBIGUOUS"
