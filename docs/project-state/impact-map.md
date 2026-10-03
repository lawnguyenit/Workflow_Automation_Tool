# Impact map

| Area | Current owner | Current state | Future impact to track |
|---|---|---|---|
| Framework contracts / definitions / instances / workflow graph | Workflow lead | Implemented; unit-verified | Compatibility/version policy and more runtime adapters |
| Gmail/email ingestion | Input module owner | Implemented; fixture and mocked HTTP verified | OAuth lifecycle, pagination/checkpoint, live quota and volume handling |
| Document extraction | Processing module owner | Implemented; synthetic PDF fixtures verified | OCR for image-only documents, adversarial PDF/resource testing |
| LLM structured extraction | Processing module owner | Implemented; local parser and mocked API verified | Live quality/cost/privacy/model compatibility and budgets |
| PostgreSQL storage | Storage module owner | Implemented; fake DB/unit verified | Real PostgreSQL transaction/concurrency tests, pool saturation, migration and restore |
| Lead intake and event storage | Workflow lead | Implemented; unit tests and sequential Docker PostgreSQL API integration verified | Identity-level contact dedupe, CRM upsert conflicts, simultaneous race/load tests, policy/retention, source connector |
| Google Form source | Workflow lead | Earlier Sheets row adapter remains optional; selected Forms API mapper/composition and inactive polling export added with synthetic verification | Read-only OAuth consent, live schema/title confirmation, n8n import/runtime, static cursor overlap safety, Forms API quota/backlog and live source-to-PostgreSQL run |
| Lead qualification | Workflow lead | Structured OpenAI module, durable PostgreSQL event ledger/cache, cross-worker claim, explicit UTC-day call-count cap, and mock/API transport tests; no live API account | Operator reconciliation procedure for unknown claims, owner-set cap/provider USD budget, rubric governance, live model quality/cost/privacy tests |
| GoHighLevel CRM | Workflow lead | Contact upsert, additive tag update, and opportunity adapters implemented; synthetic HTTP tests; no live account; none composed into lead graph | Contact duplicate policy, tag labels, account-specific pipeline/custom-field mapping, composed source-to-CRM edges, credential scope, provider quota behavior |
| Notification | Output module owner | Implemented; outbox/provider mocks verified | Live Slack behavior and manual reconciliation procedure |
| n8n / Python / HTTP executors | Runtime owner | Python/HTTP executor + static n8n templates; n8n 2.41.6 and an authenticated Assistant browser session observed; host REST call returns 401 without API auth | Configure read-only Google OAuth in UI, import/run the Forms workflow, verify saved execution policy and future version compatibility |
| Portfolio workflows and test fixtures | Workflow owner | Invoice dry-run plus manual, Sheets-shaped, and Forms API-shaped lead paths verified with synthetic Docker PostgreSQL | User review; live Forms API/n8n, GHL, live LLM/email, and stop-condition behavior |
| Reliability and capacity | Module owner + lead | Partial guarantees implemented and unit-verified | Queue/backpressure, real DB contention, load limits, rate-limit scheduler, production telemetry |
| Research role | Workflow lead invokes; researcher advises | Role proposed | Source freshness, claim applicability, plan changes only by lead decision |
| Security and data handling | Lead + implementation owner | Workspace rules apply | Secret redaction, PII minimization, least privilege, retention, tenant boundaries |
| Git/release notes | Maintainer | Template in module-quality docs | Accurate guarantee claims, runtime compatibility, limits, migration and rollback notes |
