# Verification and gaps

## Checks completed

- GHL contact upsert adapter added after review of official HighLevel v3 upsert, private integration, tag, and rate-limit docs. Its dedicated suite returned **8 passed**; the full repository suite returned **84 passed, 1 Starlette deprecation warning**. `compileall` and `git diff --check` passed. Synthetic transport tests only; no live GHL request or CRM side effect was made.

- Final focused suite after independent-audit fixes: `.venv\Scripts\python.exe -m pytest -q` returned **76 passed, 1 Starlette deprecation warning**; `compileall` and `git diff --check` passed.
- Applied `infra/postgres/migrations/002-lead-management.sql` to the already-running Compose PostgreSQL volume. The repeatable migration created `workflow_leads` and recorded schema version 1.
- Rebuilt/restarted the Docker API image at version 0.2.0 and exercised `POST /v1/workflow-instances/portfolio.lead_management_local/runs` with `MOCK` plus the local API bearer key, using synthetic-only records. Observed one new row (`STORED`), a repeated same event (`DUPLICATE`, no second row), and a changed payload with the same key (`PAYLOAD_CONFLICT_REVIEW`, first payload preserved). Each outcome ended in `REVIEW_REQUIRED`.
- Submitted a synthetic malformed/incomplete lead. API output and stored row reported `REVIEW_REQUIRED` with exact reasons: missing name/service/description, invalid email shape, qualification rubric not configured. No values were fabricated.
- Repeated the event after the final status change: workflow and store returned `REVIEW_REQUIRED` while retaining state `DUPLICATE`. Response payload omitted the synthetic email. Direct health checks returned `ok` for API and n8n; PostgreSQL was healthy.
- Static tests confirm the lead n8n export targets the correct local workflow instance, uses synthetic `.invalid` data, remains inactive, defaults to `DRY_RUN`, and disables saved execution payloads.
- Focused boundary tests now confirm direct module calls expose only qualification status/reasons (not lead identity) and that malformed/oversized qualification data or a forged fingerprint is rejected before the storage connection opens.
- In the browser, the test Google Form response page showed 0 responses and that it must be published before accepting responses; the linked response Sheet exists. The form was not published.
- Opening `http://127.0.0.1:5678` showed the n8n sign-in page. Workflow import/execution was not attempted without the owner's n8n login.

- `.venv\Scripts\python.exe -m pytest -q`: **65 passed**, including definition/instance bindings, per-instance configuration, HTTP executor addressing/auth headers, email/PDF input, LLM/validation, storage/outbox, notification retry behavior, API auth/live-Gmail gate, and n8n workflow topology.
- `.venv\Scripts\python.exe -m compileall -q src tests`: passed.
- `uv sync --extra dev`: passed; resolved/audited 37 packages.
- Started Uvicorn locally and called `GET /healthz`: returned `{"status":"ok"}`.
- Posted to `/v1/workflow-instances/portfolio.intelligent_document_intake/runs` with `{}` through the live local HTTP server: returned `SUCCESS`, all six steps, extracted synthetic `INV-1001` at USD 1200, store state `PREVIEWED`, notification state `PREVIEWED`, and `DRY_RUN` context. No DB or provider side effects were requested.
- Posted to `/v1/instances/portfolio.email_ingest/runs` through the live local HTTP server: returned `SUCCESS` and one synthetic item.
- API authorization tests confirm `MOCK`/`REAL` and live Gmail reads are blocked without `WORKFLOW_API_KEY`; `DRY_RUN` remains available for the n8n template. The API does not enforce that preview payloads are synthetic, so preview data still needs privacy controls.
- The n8n demo JSON parses, and a static topology test checks six module HTTP nodes, six success gates, the stop branch, dry-run contexts, and disabled execution saving. This does not verify n8n import or runtime behavior.
- `docker compose config -q` passed using temporary placeholder environment values; this validates Compose syntax/interpolation only, not image pulls/builds or service startup.
- `docker compose up --build -d` succeeded with Docker Desktop 4.70.0, Docker Engine 29.4.0, and Compose 5.1.2. The first container workflow run exposed that `fixtures/` was missing from the image; `Dockerfile` was fixed to copy it, then the image was rebuilt.
- In the running Compose stack, PostgreSQL reported healthy, API `/healthz` returned `200`, n8n `/healthz` returned `200`, and the workflow API completed all six synthetic `DRY_RUN` steps.
- Rechecked on 2026-10-03: `.venv\\Scripts\\python.exe -m pytest -q` returned **65 passed, 1 Starlette deprecation warning**; `docker compose ps` showed API and n8n up and PostgreSQL healthy; API and n8n `/healthz` both returned `{"status":"ok"}`.
- Authenticated `MOCK` execution ran twice through the API and real local PostgreSQL: both workflows succeeded; the second storage result reported `duplicate=true`; PostgreSQL contained one invoice row and one notification outbox row. Slack was mocked; no external provider was called.
- Pytest emitted one Starlette deprecation warning about its current HTTPX test-client integration; tests still passed.

## Not checked / not implemented

- No live Gmail, Outlook, OpenAI, Slack, GHL/CRM, Google Forms/Sheets response ingestion, or client environment was exercised. Invoice workflow provider routes remain mocked; lead PostgreSQL was exercised with synthetic data.
- Unauthenticated `DRY_RUN`/`REPLAY` avoids database writes and provider outputs, but does not impose a synthetic-data-only restriction on caller payloads or verify that replay inputs were captured and sanitized. Keep the API loopback/private, sanitize replay data before submission, and treat preview results as potentially sensitive.
- `WORKFLOW_API_KEY` is a single shared bearer key, not per-user identity/authorization, role separation, or TLS. Compose host ports bind to loopback; a production deployment still needs its own access and transport controls.
- n8n workflow import/execution, live Gmail/OpenAI/Slack, and credential-backed provider workflows are unverified. The database was exercised for sequential idempotency, but concurrent contention/race behavior was not tested.
- Lead workflow n8n UI import/execution, Google Forms/Sheets trigger ingestion, email sending, GHL CRM, live OpenAI qualification, reply/book/opt-out/manual-pause suppression, and public HTTPS ingress are unverified or unavailable.
- The local PostgreSQL integration covered sequential retries only, not simultaneous write races, load/capacity, connection pool saturation, or backup/restore.
- No lead email was sent and no CRM account was written. Four synthetic event rows are retained locally; no real prospect data was used.
- OCR, provider-call budget/cache, async queue/backpressure, connection pooling, automatic retry scheduler, retention/migration jobs, load/capacity tests, and production monitoring are outside this initial implementation.
- Git is initialized on local `main`, with `origin` configured to the provided GitHub repository.

## Review status

- Independent read-only audit of the lead reference is recorded in `lead-management-audit.md`. It confirms the full Upwork brief is not delivered and records the available local integration evidence, account blockers, and remaining risks.
- Two initial findings (intake review state exposure and unbounded storage qualification shape) were fixed; an independent source re-check confirmed those fixes. The 76-test run and final Docker integration are implementation-lead evidence, not independently rerun by the auditor.
- User review/acceptance of the local reference and remaining gaps is pending.
