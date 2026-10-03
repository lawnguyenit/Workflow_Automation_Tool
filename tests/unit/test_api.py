from fastapi.testclient import TestClient
from workflow_automation.api import create_app
from workflow_automation.contracts import ExecutionContext, RunMode


def test_catalog_and_health_are_available():
    client = TestClient(create_app())
    assert client.get("/healthz").json() == {"status": "ok"}
    catalog = client.get("/v1/catalog").json()
    assert {module["module_id"] for module in catalog["modules"]} == {
        "email.ingest", "document.extract", "llm.structured_extract", "data.validate",
        "storage.record", "notify.send", "gmail.send", "lead.intake", "lead.store",
        "lead.qualify", "source.google_form_response", "crm.ghl_upsert_contact",
        "crm.ghl_upsert_opportunity",
    }


def test_gmail_send_instance_is_previewable_without_credentials_or_side_effects():
    client = TestClient(create_app())
    response = client.post("/v1/instances/portfolio.gmail_send/runs", json={
        "payload": {"idempotency_key": "synthetic-event:ack", "to": "person@example.invalid",
                    "subject": "Synthetic test", "text_body": "No real email is sent in DRY_RUN."},
    })
    assert response.status_code == 200
    assert response.json()["business_result"] == {
        "state": "PREVIEWED", "message_id": None, "duplicate": False, "side_effect": False,
    }
    assert "person@example.invalid" not in response.text


def test_google_form_instance_runs_mapped_lead_graph_and_redacts_source_pii(monkeypatch):
    monkeypatch.setenv("GOOGLE_FORM_SPREADSHEET_ID", "syntheticSpreadsheet123")
    monkeypatch.setenv("GOOGLE_FORM_SHEET_ID", "99017")
    client = TestClient(create_app())
    response = client.post("/v1/workflow-instances/portfolio.lead_management_google_form/runs", json={
        "payload": {
            "row_number": 2,
            "change_type": "added",
            "row": {
                "Dấu thời gian": "2026-10-03 10:00:00",
                "Tên đầy đủ": "Taylor Example",
                "Email address": "taylor@example.invalid",
                "Phone (optional)": "+15550102030",
                "Service requested": "Workflow automation",
                "Describe your request": "Synthetic test request.",
                "row_number": 2,
                "change_type": "added",
            },
        },
    })
    assert response.status_code == 200
    body = response.json()
    assert body["context"]["mode"] == "DRY_RUN"
    assert body["steps"]["map"]["business_result"] == {"mapped": True}
    assert body["steps"]["intake"]["business_result"]["qualification"]["status"] == "REVIEW_REQUIRED"
    assert body["steps"]["store"]["business_result"]["state"] == "PREVIEWED"
    assert "Taylor Example" not in response.text
    assert "taylor@example.invalid" not in response.text
    assert "Synthetic test request" not in response.text


def test_google_form_mapper_endpoint_redacts_pii(monkeypatch):
    monkeypatch.setenv("GOOGLE_FORM_SPREADSHEET_ID", "syntheticSpreadsheet123")
    monkeypatch.setenv("GOOGLE_FORM_SHEET_ID", "99017")
    client = TestClient(create_app())
    response = client.post("/v1/instances/portfolio.google_form_response_map/runs", json={
        "payload": {
            "row_number": 2,
            "change_type": "added",
            "row": {
                "Tên đầy đủ": "Taylor Example", "Email address": "taylor@example.invalid",
                "Phone (optional)": "+15550102030", "Service requested": "Automation",
                "Describe your request": "Synthetic test.",
            },
        },
    })
    assert response.status_code == 200
    assert response.json()["business_result"] == {"mapped": True}
    assert "Taylor Example" not in response.text
    assert "taylor@example.invalid" not in response.text


def test_ghl_module_endpoint_is_exposed_without_leaking_runtime_token(monkeypatch):
    monkeypatch.setenv("GHL_LOCATION_ID", "synthetic-location")
    monkeypatch.setenv("GHL_PRIVATE_INTEGRATION_TOKEN", "synthetic-secret")
    client = TestClient(create_app())
    catalog = client.get("/v1/catalog").json()
    module_instance = next(item for item in catalog["instances"]
                           if item["instance_id"] == "portfolio.ghl_contact_upsert")
    assert module_instance["config"] == {"location_id": "synthetic-location"}
    assert "synthetic-secret" not in str(catalog)

    response = client.post("/v1/instances/portfolio.ghl_contact_upsert/runs", json={
        "payload": {"contact": {"email": "ada@example.invalid", "name": "Ada Example"}},
    })
    assert response.status_code == 200
    assert response.json()["business_result"] == {"state": "PREVIEWED", "side_effect": False}

    module_instance = next(item for item in catalog["instances"]
                           if item["instance_id"] == "portfolio.ghl_opportunity_upsert")
    assert module_instance["config"] == {"location_id": "synthetic-location"}
    opportunity_response = client.post("/v1/instances/portfolio.ghl_opportunity_upsert/runs", json={
        "payload": {"opportunity": {
            "contactId": "synthetic-contact", "pipelineId": "synthetic-pipeline",
            "pipelineStageId": "synthetic-stage", "name": "Synthetic opportunity", "status": "open",
        }},
    })
    assert opportunity_response.status_code == 200
    assert opportunity_response.json()["business_result"] == {"state": "PREVIEWED", "side_effect": False}


def test_portfolio_runs_end_to_end_in_dry_run_without_external_services():
    client = TestClient(create_app())
    response = client.post("/v1/workflows/intelligent_document_intake/runs", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["context"]["mode"] == "DRY_RUN"
    assert set(body["steps"]) == {"email", "document", "extract", "validate", "store", "notify"}
    record = body["steps"]["store"]["business_result"]["records"][0]
    assert record["persistence_state"] == "PREVIEWED"
    assert body["steps"]["notify"]["business_result"]["notifications"][0]["status"] == "PREVIEWED"
    assert body["steps"]["email"]["business_result"] == {}
    assert body["steps"]["document"]["business_result"] == {}
    assert "content_base64" not in response.text
    assert "Invoice Number: INV-1001" not in response.text


def test_instance_endpoint_rejects_unknown_instance():
    client = TestClient(create_app())
    response = client.post("/v1/instances/does.not.exist/runs", json={"payload": {}})
    assert response.status_code == 404


def test_workflow_instance_endpoint_uses_sanitized_workflow_output():
    client = TestClient(create_app())
    response = client.post("/v1/workflow-instances/portfolio.intelligent_document_intake/runs", json={})
    assert response.status_code == 200
    assert response.json()["workflow_instance_id"] == "portfolio.intelligent_document_intake"
    assert "content_base64" not in response.text


def test_lead_workflow_dry_run_is_sanitized_and_review_first():
    client = TestClient(create_app())
    response = client.post("/v1/workflow-instances/portfolio.lead_management_local/runs", json={
        "payload": {"lead": {
            "source_system": "test", "source_account": "local", "source_event_id": "test-1",
            "name": "Taylor Example", "email": "taylor@example.invalid", "phone": "",
            "service_requested": "Automation", "description": "Synthetic only",
        }}
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "REVIEW_REQUIRED"
    assert body["steps"]["intake"]["business_result"]["qualification"]["status"] == "REVIEW_REQUIRED"
    assert body["steps"]["qualify"]["business_result"]["qualification"]["status"] == "REVIEW_REQUIRED"
    assert body["steps"]["qualify"]["status"] == "PARTIAL"
    assert body["result"]["state"] == "PREVIEWED"
    assert body["steps"]["store"]["status"] == "REVIEW_REQUIRED"
    assert "Taylor Example" not in response.text
    assert "taylor@example.invalid" not in response.text


def test_lead_workflow_applies_explicit_qualification_fixture_without_provider_side_effects():
    client = TestClient(create_app())
    response = client.post("/v1/workflow-instances/portfolio.lead_management_local/runs", json={
        "payload": {
            "lead": {
                "source_system": "test", "source_account": "local", "source_event_id": "test-qualified",
                "name": "Taylor Example", "email": "taylor@example.invalid", "phone": "",
                "service_requested": "Automation", "description": "Route web enquiries.",
            },
            "criteria": {
                "rubric": "Classify only fit to the automation service described in the enquiry.",
                "classifications": [{"name": "qualified", "description": "Fits the offered service."}],
                "required_fields": ["name", "email", "service_requested", "description"],
            },
            "mock_qualification": {
                "classification": "qualified", "summary": "Wants web enquiry routing.",
                "confidence": 0.9, "missing_information": [], "reasons": [],
            },
        }
    })
    assert response.status_code == 200
    body = response.json()
    assert body["steps"]["qualify"]["status"] == "SUCCESS"
    assert body["steps"]["qualify"]["business_result"]["qualification"] == {
        "status": "CLASSIFIED", "reasons": [], "classification": "qualified",
        "summary": "Wants web enquiry routing.", "model_used": "fixture",
    }
    assert body["steps"]["store"]["business_result"]["state"] == "PREVIEWED"
    assert "Taylor Example" not in response.text
    assert "taylor@example.invalid" not in response.text


def test_direct_lead_intake_route_exposes_review_reason_but_not_lead_pii(monkeypatch):
    monkeypatch.setenv("WORKFLOW_API_KEY", "test-only-api-key")
    client = TestClient(create_app())
    response = client.post("/v1/instances/portfolio.lead_intake/runs", headers={
        "Authorization": "Bearer test-only-api-key",
    }, json={
        "payload": {"lead": {
            "source_system": "test", "source_account": "local", "source_event_id": "test-2",
            "name": "Taylor Example", "email": "taylor@example.invalid", "phone": "",
            "service_requested": "Automation", "description": "Synthetic only",
        }},
        "context": ExecutionContext(workflow_id="lead_management_intake", mode=RunMode.MOCK).model_dump(mode="json"),
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PARTIAL"
    assert body["business_result"]["qualification"]["status"] == "REVIEW_REQUIRED"
    assert "qualification_criteria_not_configured" in body["business_result"]["qualification"]["reasons"]
    assert "Taylor Example" not in response.text
    assert "taylor@example.invalid" not in response.text


def test_real_execution_is_blocked_until_api_key_is_configured(monkeypatch):
    monkeypatch.delenv("WORKFLOW_API_KEY", raising=False)
    client = TestClient(create_app())
    response = client.post("/v1/workflows/intelligent_document_intake/runs", json={
        "context": ExecutionContext(workflow_id="intelligent_document_intake", mode=RunMode.REAL).model_dump(mode="json")
    })
    assert response.status_code == 503
    assert response.json()["detail"] == "This execution requires WORKFLOW_API_KEY"


def test_configured_api_key_protects_run_routes(monkeypatch):
    monkeypatch.setenv("WORKFLOW_API_KEY", "test-only-api-key")
    client = TestClient(create_app())
    # The safe dry-run n8n template works without injecting secrets into exported JSON.
    assert client.post("/v1/workflows/intelligent_document_intake/runs", json={}).status_code == 200
    mock_context = ExecutionContext(workflow_id="intelligent_document_intake", mode=RunMode.MOCK).model_dump(mode="json")
    assert client.post("/v1/workflows/intelligent_document_intake/runs", json={"context": mock_context}).status_code == 401
    assert client.post("/v1/workflows/intelligent_document_intake/runs", json={"context": mock_context},
                       headers={"Authorization": "Bearer test-only-api-key"}).status_code == 200


def test_live_gmail_read_requires_api_key_even_in_dry_run(monkeypatch):
    monkeypatch.delenv("WORKFLOW_API_KEY", raising=False)
    client = TestClient(create_app())
    response = client.post("/v1/instances/portfolio.email_ingest/runs", json={
        "payload": {"source": "gmail", "query": "has:attachment", "max_results": 1,
                    "source_account": "fixture-account"},
        "context": ExecutionContext(workflow_id="intelligent_document_intake", mode=RunMode.DRY_RUN).model_dump(mode="json"),
    })
    assert response.status_code == 503
