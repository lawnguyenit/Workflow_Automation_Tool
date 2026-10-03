import json
from pathlib import Path


EXPORT = Path(__file__).resolve().parents[2] / "workflows" / "n8n" / "lead-management-google-form.local.json"
MANUAL_PREVIEW = Path(__file__).resolve().parents[2] / "workflows" / "n8n" / "lead-management-google-form.manual-preview.json"


def test_google_form_n8n_export_only_routes_new_rows_to_local_preview():
    workflow = json.loads(EXPORT.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}
    trigger = nodes["Google Form response changes"]
    filter_node = nodes["Only new response rows"]
    request = nodes["Preview lead intake"]

    assert workflow["active"] is False
    assert "credentials" not in trigger
    assert trigger["parameters"]["event"] == "anyUpdate"
    assert trigger["parameters"]["documentId"]["value"].startswith("REPLACE_")
    assert trigger["parameters"]["sheetName"]["value"].startswith("REPLACE_")
    condition = filter_node["parameters"]["conditions"]["conditions"][0]
    assert condition["leftValue"] == "={{ $json.change_type }}"
    assert condition["rightValue"] == "added"
    assert "portfolio.lead_management_google_form" in request["parameters"]["url"]
    assert "Number($json.row_number)" in request["parameters"]["jsonBody"]
    assert "change_type: $json.change_type" in request["parameters"]["jsonBody"]
    assert "mode: 'DRY_RUN'" in request["parameters"]["jsonBody"]
    assert "Authorization" not in json.dumps(workflow)


def test_manual_google_form_shape_preview_is_synthetic_private_and_non_mutating():
    workflow = json.loads(MANUAL_PREVIEW.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}
    request = nodes["Preview Google Form-shaped lead"]
    body = request["parameters"]["jsonBody"]
    assert workflow["active"] is False
    assert "Google Form" in workflow["name"]
    assert "portfolio.lead_management_google_form" in request["parameters"]["url"]
    assert "mode: 'DRY_RUN'" in body
    assert "example.invalid" in body
    assert "Synthetic n8n preview only." in body
    assert "Authorization" not in json.dumps(workflow)
