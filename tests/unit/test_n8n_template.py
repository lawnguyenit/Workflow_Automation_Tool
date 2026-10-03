import json
from pathlib import Path


def test_demo_composes_every_module_instance_and_gates_non_success_states():
    path = Path(__file__).resolve().parents[2] / "workflows" / "n8n" / "intelligent-document-intake.demo.json"
    workflow = json.loads(path.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}
    http_nodes = [node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.httpRequest"]
    if_nodes = [node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.if"]

    assert len(http_nodes) == 6
    assert len(if_nodes) == 6
    assert all("/v1/instances/portfolio." in node["parameters"]["url"] for node in http_nodes)
    assert all('"mode": "DRY_RUN"' in node["parameters"]["jsonBody"] for node in http_nodes)
    assert all(workflow["connections"][node["name"] + " succeeded?"]["main"][1][0]["node"] == "Stop for review"
               for node in http_nodes)
    assert workflow["settings"]["saveManualExecutions"] is False
    assert workflow["settings"]["saveDataErrorExecution"] == "none"
    assert workflow["settings"]["saveDataSuccessExecution"] == "none"
    assert nodes["Stop for review"]["type"] == "n8n-nodes-base.stopAndError"


def test_lead_management_template_is_private_synthetic_preview():
    path = Path(__file__).resolve().parents[2] / "workflows" / "n8n" / "lead-management-local.demo.json"
    workflow = json.loads(path.read_text(encoding="utf-8"))
    http_nodes = [node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.httpRequest"]
    assert len(http_nodes) == 1
    request = http_nodes[0]["parameters"]
    assert request["url"].endswith("/v1/workflow-instances/portfolio.lead_management_local/runs")
    assert "synthetic-lead-001" in request["jsonBody"]
    assert "taylor@example.invalid" in request["jsonBody"]
    assert "DRY_RUN" in request["jsonBody"]
    assert workflow["active"] is False
    assert workflow["settings"]["saveDataErrorExecution"] == "none"
    assert workflow["settings"]["saveDataSuccessExecution"] == "none"
