# Implementation state

## Current verified state

- The approved framework and synthetic Intelligent Document Intake portfolio are implemented under `src/workflow_automation/`.
- Core includes shared result/execution contracts, capability/implementation/instance/executor/workflow-definition/workflow-instance models, validated registry, Python and HTTP executors, generic workflow runner, and FastAPI module/catalog/workflow routes.
- Reference modules include fixture/Gmail read ingestion, bounded PDF text extraction, deterministic/strict-schema LLM extraction, configurable validation, PostgreSQL idempotent record storage, and Slack notification with an outbox.
- A separate lead-management local reference now includes `lead.intake` (bounded normalization, missing/invalid-field flags, fail-closed manual review) and `lead.store` (PostgreSQL event storage with a unique `(source_system, source_account, source_event_id)` key and first-write-wins conflict reporting). See `docs/modules/lead-management-reference.md`.
- A reusable `crm.ghl_upsert_contact` Python adapter targets HighLevel API v3 contact upsert. It is registered as a capability but intentionally not bound to the portfolio workflow because no account Location, duplicate policy, or field mapping is available. See `docs/modules/ghl-contact-upsert.md`.
- The lead workflow endpoint suppresses normalized lead fields in aggregate responses; synthetic lead fields are stored in the local database. Its n8n preview export is `workflows/n8n/lead-management-local.demo.json` and uses `DRY_RUN` by default.
- A private Google Form named `Lead Intake Lab - Test Only` and linked response spreadsheet were created in the user's signed-in Google account. The form remains unpublished with zero responses. The n8n UI currently shows a sign-in page; import and UI execution have not occurred.
- The user has Gmail and Outlook with 2FA, but no configured sender credential; no email has been sent. No GHL/CRM test account or OpenAI API key was identified. No CRM or LLM live operation is bound. The user's VPS hosts an online game and was not contacted or changed.
- The user confirmed Gmail and Outlook with 2FA, no known CRM/GHL, no existing lead form or public HTTPS endpoint, ChatGPT Plus only, and permits one self-addressed test message if no third party is affected. The private form remains unpublished. The VPS is excluded because it runs a game. OAuth/account connections and live outbound operations remain unconfigured.
- The workflow API defaults to `DRY_RUN`; live modes require separate credentials/configuration. The API response removes raw attachment bytes and extracted PDF text from step results.
- Workflow definitions reference module capabilities; workflow instances bind each step to a separately configured module instance. The HTTP executor targets concrete module-instance routes.
- `MOCK`/`REAL` and live Gmail reads require an optional shared bearer key; `DRY_RUN`/`REPLAY` remain available without a key for the n8n demo. The API accepts caller-supplied preview data and does not verify replay capture/sanitization. This is not per-user authorization; keep the API private and sanitize replay data before submission.
- `fixtures/` contains synthetic-only email/PDF inputs. Docker Compose, initial PostgreSQL schema, `.env.example`, and an n8n import template are present.
- `docs/modules/portfolio-modules.md` records limits, duplicates, concurrency, retries, recovery, capacity, data handling, and live-verification gaps. `docs/releases/0.1.0.md` is the release-note draft.
- The shared read-only workflow risk researcher is defined in `../.codex/agents/workflow-module-risk-researcher.toml`.

## Evidence boundary

The unit suite (84 passed after adding the GHL adapter), compilation, dependency sync, Compose build/startup, the new PostgreSQL migration, synthetic lead API runs through Docker PostgreSQL, sequential source-event idempotency, duplicate-payload conflict, incomplete-field review, PII suppression in workflow responses, n8n health endpoint, static n8n graph checks, and GHL adapter HTTP transport tests are verified. GHL was only exercised with synthetic `httpx.MockTransport`; no live GHL write occurred. Lead workflow import/execution in the n8n UI and Gmail/Outlook/GHL/OpenAI/Google Forms response ingestion are not. The database test was sequential, not a concurrent load/race test. Git is initialized on `main` with `origin` configured to the user-provided remote.

## Evidence

- Repository inspection found no existing workflow automation implementation in this tool directory; `Scraping_Tool` is a separate project.
- No CRM/GHL test Location, OpenAI API key, or email sender credential is available. No email/CRM side effect or real prospect data was used.
