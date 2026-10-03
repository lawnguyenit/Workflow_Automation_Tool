# Impact map

| Area | Current owner | Current state | Future impact to track |
|---|---|---|---|
| Framework contracts / definitions / instances / workflow graph | Workflow lead | Implemented; unit-verified | Compatibility/version policy and more runtime adapters |
| Gmail/email ingestion | Input module owner | Implemented; fixture and mocked HTTP verified | OAuth lifecycle, pagination/checkpoint, live quota and volume handling |
| Document extraction | Processing module owner | Implemented; synthetic PDF fixtures verified | OCR for image-only documents, adversarial PDF/resource testing |
| LLM structured extraction | Processing module owner | Implemented; local parser and mocked API verified | Live quality/cost/privacy/model compatibility and budgets |
| PostgreSQL storage | Storage module owner | Implemented; fake DB/unit verified | Real PostgreSQL transaction/concurrency tests, pool saturation, migration and restore |
| Lead intake and event storage | Workflow lead | Implemented; unit tests and sequential Docker PostgreSQL API integration verified | Identity-level contact dedupe, CRM upsert conflicts, simultaneous race/load tests, policy/retention, source connector |
| Notification | Output module owner | Implemented; outbox/provider mocks verified | Live Slack behavior and manual reconciliation procedure |
| n8n / Python / HTTP executors | Runtime owner | Python/HTTP executor + two static n8n templates; local n8n health verified | Lead workflow UI import/run needs n8n sign-in; execution retention and version remain unverified |
| Portfolio workflows and test fixtures | Workflow owner | Invoice dry-run plus lead local Docker PostgreSQL path verified with synthetic data | User review; GHL, live LLM/email, source-form ingestion and stop-condition behavior |
| Reliability and capacity | Module owner + lead | Partial guarantees implemented and unit-verified | Queue/backpressure, real DB contention, load limits, rate-limit scheduler, production telemetry |
| Research role | Workflow lead invokes; researcher advises | Role proposed | Source freshness, claim applicability, plan changes only by lead decision |
| Security and data handling | Lead + implementation owner | Workspace rules apply | Secret redaction, PII minimization, least privilege, retention, tenant boundaries |
| Git/release notes | Maintainer | Template in module-quality docs | Accurate guarantee claims, runtime compatibility, limits, migration and rollback notes |
