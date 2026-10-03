from __future__ import annotations

import os

from workflow_automation.definitions import (
    EXECUTOR_DEFINITIONS,
    LEAD_GOOGLE_FORM_WORKFLOW,
    LEAD_GOOGLE_FORM_WORKFLOW_INSTANCE,
    LEAD_GOOGLE_FORMS_API_WORKFLOW,
    LEAD_GOOGLE_FORMS_API_WORKFLOW_INSTANCE,
    LEAD_MANAGEMENT_WORKFLOW,
    LEAD_MANAGEMENT_WORKFLOW_INSTANCE,
    MODULE_DEFINITIONS,
    MODULE_IMPLEMENTATIONS,
    MODULE_INSTANCES,
    PORTFOLIO_WORKFLOW,
    PORTFOLIO_WORKFLOW_INSTANCE,
)
from workflow_automation.executors import PythonExecutor
from workflow_automation.infrastructure.postgres_effect_guard import PostgresExternalEffectGuard
from workflow_automation.infrastructure.postgres_lead_qualification_ledger import PostgresLeadQualificationLedger
from workflow_automation.infrastructure.postgres_source_poll_checkpoint import PostgresSourcePollCheckpoint
from workflow_automation.models import ModuleInstance
from workflow_automation.modules.crm.ghl_upsert_contact import GHLUpsertContact
from workflow_automation.modules.crm.ghl_upsert_opportunity import GHLUpsertOpportunity
from workflow_automation.modules.crm.ghl_contact_tags import GHLContactTags
from workflow_automation.modules.input.email_ingest import EmailIngest
from workflow_automation.modules.input.google_form_response import GoogleFormResponseRow
from workflow_automation.modules.input.google_forms_api_response import GoogleFormsApiResponse
from workflow_automation.modules.output.notify import NotifySend
from workflow_automation.modules.output.gmail_send import GmailSend
from workflow_automation.modules.processing.lead_intake import LeadIntake
from workflow_automation.modules.processing.lead_qualify import LeadQualify
from workflow_automation.modules.processing.lead_followup_gate import LeadFollowupGate
from workflow_automation.modules.processing.source_poll_checkpoint import SourcePollCheckpoint
from workflow_automation.modules.processing.document_extract import DocumentExtract
from workflow_automation.modules.processing.llm_structured_extract import LLMStructuredExtract
from workflow_automation.modules.processing.validate import Validate
from workflow_automation.modules.storage.postgres_store import PostgresStore
from workflow_automation.modules.storage.postgres_lead_store import PostgresLeadStore
from workflow_automation.registry import FrameworkRegistry


def build_registry(additional_module_instances=None) -> FrameworkRegistry:
    """Build the portfolio registry, applying each declared instance config."""
    registry = FrameworkRegistry()
    for item in MODULE_DEFINITIONS:
        registry.add_definition(item)
    for item in MODULE_IMPLEMENTATIONS:
        registry.add_implementation(item)
    for item in EXECUTOR_DEFINITIONS:
        registry.add_executor(item)
    registry.bind_executor("python.local", PythonExecutor())

    instance_catalog = [*MODULE_INSTANCES, *(additional_module_instances or [])]
    ghl_location_id = os.getenv("GHL_LOCATION_ID", "").strip()
    if ghl_location_id and not any(item.instance_id == "portfolio.ghl_contact_upsert"
                                   for item in instance_catalog):
        instance_catalog.append(ModuleInstance(
            instance_id="portfolio.ghl_contact_upsert",
            module="crm.ghl_upsert_contact",
            implementation="crm.ghl_upsert_contact.http.v1",
            executor_id="python.local",
            config={"location_id": ghl_location_id},
        ))
    if ghl_location_id and not any(item.instance_id == "portfolio.ghl_opportunity_upsert"
                                   for item in instance_catalog):
        instance_catalog.append(ModuleInstance(
            instance_id="portfolio.ghl_opportunity_upsert",
            module="crm.ghl_upsert_opportunity",
            implementation="crm.ghl_upsert_opportunity.http.v1",
            executor_id="python.local",
            config={"location_id": ghl_location_id},
        ))
    if ghl_location_id and not any(item.instance_id == "portfolio.ghl_contact_tags"
                                   for item in instance_catalog):
        instance_catalog.append(ModuleInstance(
            instance_id="portfolio.ghl_contact_tags",
            module="crm.ghl_contact_tags",
            implementation="crm.ghl_contact_tags.http.v1",
            executor_id="python.local",
            config={"location_id": ghl_location_id},
        ))
    stores: dict[str, PostgresStore] = {}
    qualification_call_limit = os.getenv("LEAD_LLM_MAX_CALLS_PER_DAY", "").strip()
    max_qualification_calls_per_day = int(qualification_call_limit) if qualification_call_limit else None
    for item in instance_catalog:
        registry.add_instance(item)
        config = item.config
        if item.module == "email.ingest":
            module = EmailIngest(
                max_attachment_bytes=config.get("max_attachment_bytes", 10 * 1024 * 1024),
                max_total_attachment_bytes=config.get("max_total_attachment_bytes", 25 * 1024 * 1024),
                timeout_seconds=config.get("timeout_seconds", 15),
            )
        elif item.module == "source.google_form_response":
            module = GoogleFormResponseRow(
                spreadsheet_id=os.getenv("GOOGLE_FORM_SPREADSHEET_ID") or config.get("spreadsheet_id"),
                sheet_id=os.getenv("GOOGLE_FORM_SHEET_ID") or config.get("sheet_id"),
                source_account=config.get("source_account", "portfolio-lead-form"),
                header_map=config.get("header_map"),
            )
        elif item.module == "source.google_forms_api_response":
            module = GoogleFormsApiResponse(
                source_account=config.get("source_account", "portfolio-lead-form"),
                field_titles=config.get("field_titles"),
            )
        elif item.module == "state.source_poll_checkpoint":
            module = SourcePollCheckpoint(
                store=PostgresSourcePollCheckpoint(),
                lease_seconds=config.get("lease_seconds", 900),
            )
        elif item.module == "document.extract":
            module = DocumentExtract(
                max_file_bytes=config.get("max_attachment_bytes", 10_000_000),
                max_pages=config.get("max_pages", 100),
                max_text_chars=config.get("max_text_chars", 500_000),
            )
        elif item.module == "llm.structured_extract":
            module = LLMStructuredExtract(
                model=config.get("model"), endpoint=config.get("endpoint", "https://api.openai.com/v1/responses"),
                timeout_seconds=config.get("timeout_seconds", 30),
            )
        elif item.module == "data.validate":
            module = Validate(
                required_fields=config.get("required_fields", ["document_type", "sender", "invoice_number", "amount", "currency"]),
                allowed_currencies=config.get("allowed_currencies"),
                minimum_amount=config.get("minimum_amount", 0),
                maximum_amount=config.get("maximum_amount"),
                minimum_confidence=config.get("minimum_confidence"),
            )
        elif item.module == "storage.record":
            module = PostgresStore(source_account_scope=config.get("source_account_scope"))
            stores[item.instance_id] = module
        elif item.module == "notify.send":
            store_instance_id = config.get("storage_instance_id", "portfolio.postgres_store")
            store = stores.get(store_instance_id)
            if store is None:
                raise ValueError(f"Notification instance {item.instance_id} references unavailable storage instance {store_instance_id}")
            module = NotifySend(store=store, timeout_seconds=config.get("timeout_seconds", 10))
        elif item.module == "gmail.send":
            store_instance_id = config.get("storage_instance_id", "portfolio.postgres_store")
            store = stores.get(store_instance_id)
            if store is None:
                raise ValueError(f"Gmail instance {item.instance_id} references unavailable storage instance {store_instance_id}")
            module = GmailSend(
                store=store,
                account_scope=config.get("account_scope", "default-gmail"),
                timeout_seconds=config.get("timeout_seconds", 15),
            )
        elif item.module == "lead.intake":
            module = LeadIntake()
        elif item.module == "lead.qualify":
            module = LeadQualify(
                minimum_confidence=config.get("minimum_confidence", 0.75),
                timeout_seconds=config.get("timeout_seconds", 30),
                max_input_chars=config.get("max_input_chars", 8_000),
                qualification_ledger=PostgresLeadQualificationLedger(),
                max_calls_per_day=max_qualification_calls_per_day,
            )
        elif item.module == "lead.store":
            module = PostgresLeadStore()
        elif item.module == "lead.followup_gate":
            module = LeadFollowupGate()
        elif item.module == "crm.ghl_upsert_contact":
            module = GHLUpsertContact(
                location_id=config.get("location_id", ""),
                timeout_seconds=config.get("timeout_seconds", 15),
            )
        elif item.module == "crm.ghl_upsert_opportunity":
            module = GHLUpsertOpportunity(
                location_id=config.get("location_id", ""),
                timeout_seconds=config.get("timeout_seconds", 15),
                effect_guard=PostgresExternalEffectGuard(),
            )
        elif item.module == "crm.ghl_contact_tags":
            module = GHLContactTags(
                location_id=config.get("location_id", ""),
                timeout_seconds=config.get("timeout_seconds", 15),
                effect_guard=PostgresExternalEffectGuard(),
            )
        else:
            raise ValueError(f"No module factory is registered for capability {item.module}")
        registry.bind_instance_module(item.instance_id, module)
    registry.add_workflow(PORTFOLIO_WORKFLOW)
    registry.add_workflow_instance(PORTFOLIO_WORKFLOW_INSTANCE)
    registry.add_workflow(LEAD_MANAGEMENT_WORKFLOW)
    registry.add_workflow_instance(LEAD_MANAGEMENT_WORKFLOW_INSTANCE)
    registry.add_workflow(LEAD_GOOGLE_FORM_WORKFLOW)
    registry.add_workflow_instance(LEAD_GOOGLE_FORM_WORKFLOW_INSTANCE)
    registry.add_workflow(LEAD_GOOGLE_FORMS_API_WORKFLOW)
    registry.add_workflow_instance(LEAD_GOOGLE_FORMS_API_WORKFLOW_INSTANCE)
    return registry
