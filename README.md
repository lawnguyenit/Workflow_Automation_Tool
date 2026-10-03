# Workflow Automation Modules

## Purpose

Build a reusable library of workflow module contracts and, when jobs require them, concrete implementations. n8n is the main expected target, while the contracts remain usable by other platforms and code-based workflows.

## Working rule

Keep the capability model platform-neutral and implementations replaceable. The initial reference system is the Intelligent Document Intake Pipeline. A second local lead-management reference now exercises webhook-shaped intake, manual-review status, and PostgreSQL source-event idempotency. These are portfolio systems, not client deployments; document any provider connection as unverified until credentials and target environments are available. Grow the wider catalog from real job demand.

## Pipeline areas

- **Input:** Gmail, webhooks, Google Drive, Dropbox, forms.
- **Processing:** OCR, PDF parsing, validation, LLM extraction, classification, deduplication.
- **Storage:** PostgreSQL, Supabase, Sheets.
- **Output:** Slack, email, CRM, calendar, API.
- **Cross-cutting operations:** deployment, logging, retries, monitoring, capacity, concurrency, security, recovery, and replay.

These are candidate catalog areas, not commitments to implement every connector.

## Module quality record

Each future module should state its purpose and versioned contract; accepted inputs and returned outputs; dependencies and setup; expected volume and limits; idempotency and duplicate semantics; concurrent-write behavior; retry and timeout rules; partial-failure and recovery behavior; logs, metrics, and safe diagnostics; credential and data-handling rules; tested failure cases; compatibility and rollback notes; and known gaps.

See [the project plan](docs/plan.md) and [project state](docs/project-state/README.md).

The draft contract and reusable module checklist are in [module-contract.md](docs/module-contract.md) and [module-quality-template.md](docs/module-quality-template.md).

## Run the synthetic portfolio

Requirements: Python 3.12+, `uv`, and no provider credentials for the dry-run path.

```powershell
uv sync --extra dev
uv run uvicorn workflow_automation.api:app --app-dir src --host 127.0.0.1 --port 8000
```

In another terminal, run the full synthetic graph without database writes or provider calls:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/workflows/intelligent_document_intake/runs -ContentType 'application/json' -Body '{}'
```

The API returns sanitized step states and invoice fields; it omits attachment bytes and extracted PDF text from its aggregate workflow response. `/healthz` is a process health check, and `/v1/catalog` lists module definitions, implementations, instances, workflow definitions, and workflow instances. `POST /v1/instances/{instance_id}/runs` lets external orchestrators run one module, while `/v1/workflow-instances/{workflow_instance_id}/runs` runs a concrete workflow binding. `MOCK`/`REAL` and any live Gmail read require `WORKFLOW_API_KEY` and `Authorization: Bearer <key>`; when the key is unset those operations are rejected. `DRY_RUN`/`REPLAY` requests work without a key so the n8n template remains runnable. The API does not enforce synthetic-only preview payloads or verify that `REPLAY` inputs were captured and sanitized; they may contain sensitive business data, so keep the service on loopback/private networking and sanitize replay data before submission. Do not expose this API publicly without a stronger deployment access boundary.

For an opt-in PostgreSQL/n8n local stack, copy `.env.example` to `.env`, replace the PostgreSQL and n8n local-only secrets, then run `docker compose up --build`. Set a long random `WORKFLOW_API_KEY` before allowing `MOCK`/`REAL` calls or live Gmail reads. Use a URL-safe PostgreSQL password (letters, numbers, hyphen, underscore) because Compose passes it inside `DATABASE_URL`. The app is available on `127.0.0.1:8000` and n8n on `127.0.0.1:5678`. Import `workflows/n8n/intelligent-document-intake.demo.json`; its six HTTP nodes call the module-instance API individually in `DRY_RUN` and gate each step on `SUCCESS`. The JSON is a starter template and needs validation against the exact n8n image/version selected for a job. If the workflow is changed to `MOCK`/`REAL` or live Gmail input, configure matching Header Auth credentials on the n8n HTTP nodes. Never use the demo workflow to handle client documents without reviewing n8n execution-data retention and access first.

### Lead-management reference

`workflows/n8n/lead-management-local.demo.json` is a private, manual-trigger preview with a synthetic `.invalid` email address. It invokes `portfolio.lead_management_local` in `DRY_RUN`, so it does not write to PostgreSQL or send a message. The real local API/DB path has also been exercised separately with synthetic payloads in `MOCK` mode: first-write, same-event duplicate, changed-payload conflict, missing-field review, and API response redaction. Current results intentionally end at `REVIEW_REQUIRED`; there is no configured GHL, qualification rubric, OpenAI API, email sender, or public form callback. See [lead-management module contract and limits](docs/modules/lead-management-reference.md).

The private Google Form draft `Lead Intake Lab - Test Only` is unpublished and linked to a response Sheet. Local n8n and API ports bind to loopback. Do not publish the form or expose a public callback unless the owner explicitly requests it after the access and data-handling setup is understood. The user's VPS that hosts an online game is outside this project.

### Execution modes

- `DRY_RUN`: preview extraction and validation; PostgreSQL writes and output side effects are skipped. The API does not restrict inputs to fixtures, so caller-supplied business data may be processed.
- `REPLAY`: deterministic replay path; persistence and delivery are skipped.
- `MOCK`: deterministic/mock extraction; PostgreSQL and the notification outbox are written, but no output provider is contacted. Use only with a deliberate local/test database.
- `REAL`: Gmail read, OpenAI extraction, PostgreSQL, and Slack can run only when each is configured. This mode is not an end-to-end claim and needs per-client authorization, policy, credentials, retention, and operational setup.

Details on duplicate handling, concurrency, limits, recovery, and unverified integrations are in [portfolio module guarantees](docs/modules/portfolio-modules.md). Release notes: [0.1.0](docs/releases/0.1.0.md), [0.2.0](docs/releases/0.2.0.md).
