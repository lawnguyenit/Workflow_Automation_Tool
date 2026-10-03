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
