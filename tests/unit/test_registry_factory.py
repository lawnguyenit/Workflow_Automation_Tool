from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.models import ModuleInstance
from workflow_automation.modules.processing.validate import Validate
from workflow_automation.registry_factory import build_registry


def test_same_implementation_can_have_separate_configured_module_instances():
    second = ModuleInstance(instance_id="tenant.invoice_validate", module="data.validate",
        implementation="data.validate.python.v1", executor_id="python.local",
        config={"required_fields": ["invoice_number", "currency"], "minimum_amount": 100})
    registry = build_registry(additional_module_instances=[second])
    first_module = registry.instance_modules["portfolio.invoice_validate"]
    second_module = registry.instance_modules["tenant.invoice_validate"]
    assert isinstance(first_module, Validate)
    assert isinstance(second_module, Validate)
    assert first_module is not second_module
    assert first_module.required_fields != second_module.required_fields
    assert second_module.minimum_amount == 100


def test_ghl_instance_is_bound_only_when_location_is_configured(monkeypatch):
    monkeypatch.delenv("GHL_LOCATION_ID", raising=False)
    without_location = build_registry()
    assert "portfolio.ghl_contact_upsert" not in without_location.instances

    monkeypatch.setenv("GHL_LOCATION_ID", "synthetic-location")
    monkeypatch.setenv("GHL_PRIVATE_INTEGRATION_TOKEN", "synthetic-token")
    with_location = build_registry()
    instance = with_location.instances["portfolio.ghl_contact_upsert"]
    assert instance.module == "crm.ghl_upsert_contact"
    assert instance.config == {"location_id": "synthetic-location"}
    assert "synthetic-token" not in str(instance.model_dump())
    opportunity_instance = with_location.instances["portfolio.ghl_opportunity_upsert"]
    assert opportunity_instance.module == "crm.ghl_upsert_opportunity"
    assert opportunity_instance.config == {"location_id": "synthetic-location"}
    assert "synthetic-token" not in str(opportunity_instance.model_dump())
    opportunity = with_location.instance_modules[opportunity_instance.instance_id].execute(
        {"opportunity": {"contactId": "contact-1", "pipelineId": "pipeline-1",
                         "pipelineStageId": "stage-1", "name": "Synthetic", "status": "open"}},
        ExecutionContext(workflow_id="ghl_test", mode=RunMode.DRY_RUN),
    )
    assert opportunity.business_result["state"] == "PREVIEWED"
    result = with_location.instance_modules[instance.instance_id].execute(
        {"contact": {"email": "ada@example.invalid"}},
        ExecutionContext(workflow_id="ghl_test", mode=RunMode.DRY_RUN),
    )
    assert result.business_result["state"] == "PREVIEWED"
