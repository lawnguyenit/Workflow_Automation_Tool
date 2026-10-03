import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POLLING_EXPORT = ROOT / "workflows" / "n8n" / "lead-management-google-forms-api.local.json"
MANUAL_EXPORT = ROOT / "workflows" / "n8n" / "lead-management-google-forms-api.manual-preview.json"


def test_polling_export_is_inactive_read_only_paginated_and_checkpointed_after_success():
    workflow = json.loads(POLLING_EXPORT.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}
    assert workflow["active"] is False
    assert workflow["settings"]["saveDataSuccessExecution"] == "none"
    assert workflow["settings"]["saveDataErrorExecution"] == "none"
    assert "credentials" not in json.dumps(workflow)
    assert "REPLACE_WITH_PRIVATE_GOOGLE_FORM_ID" in nodes["Read cursor and form config"]["parameters"]["jsCode"]
    assert "forms.googleapis.com/v1/forms/" in nodes["Read Form question schema"]["parameters"]["url"]
    assert "genericAuthType" in nodes["Read Form question schema"]["parameters"]
    pagination = nodes["List Form responses"]["parameters"]["options"]["pagination"]["pagination"]
    assert pagination["paginationMode"] == "updateAParameterInEachRequest"
    assert pagination["maxRequests"] == 20
    assert pagination["parameters"]["parameters"][0]["name"] == "pageToken"
    map_code = nodes["Prepare response events"]["parameters"]["jsCode"]
    assert "finalPage.nextPageToken" in map_code
    assert "response.responseId" in map_code
    cursor_code = nodes["Advance cursor after successful storage"]["parameters"]["jsCode"]
    assert "['STORED', 'DUPLICATE', 'PREVIEWED']" in cursor_code
    assert "mode !== 'MOCK' && mode !== 'REAL'" in cursor_code
    assert "state.lastSubmittedTime" in cursor_code
    assert workflow["connections"]["Run local lead workflow"]["main"][0][0]["node"] == "Advance cursor after successful storage"


def test_manual_forms_api_preview_is_synthetic_and_does_not_need_google_oauth():
    workflow = json.loads(MANUAL_EXPORT.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}
    request = nodes["Preview synthetic Form response"]
    assert workflow["active"] is False
    assert "credentials" not in json.dumps(workflow)
    body = request["parameters"]["jsonBody"]
    assert "syntheticGoogleForm123" in body
    assert "syntheticResponse456" in body
    assert "example.invalid" in body
    assert "Synthetic Forms API preview only." in body
    assert "mode: 'DRY_RUN'" in body
    assert "Authorization" not in json.dumps(workflow)
