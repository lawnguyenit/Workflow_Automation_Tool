# Verification and gaps

## Checks completed

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

- No live Gmail, OpenAI, Slack, PostgreSQL, or client environment was available or exercised. The Gmail route is mocked; storage tests use a fake connection.
- Unauthenticated `DRY_RUN`/`REPLAY` avoids database writes and provider outputs, but does not impose a synthetic-data-only restriction on caller payloads or verify that replay inputs were captured and sanitized. Keep the API loopback/private, sanitize replay data before submission, and treat preview results as potentially sensitive.
- `WORKFLOW_API_KEY` is a single shared bearer key, not per-user identity/authorization, role separation, or TLS. Compose host ports bind to loopback; a production deployment still needs its own access and transport controls.
- n8n workflow import/execution, live Gmail/OpenAI/Slack, and credential-backed provider workflows are unverified. The database was exercised for sequential idempotency, but concurrent contention/race behavior was not tested.
- OCR, provider-call budget/cache, async queue/backpressure, connection pooling, automatic retry scheduler, retention/migration jobs, load/capacity tests, and production monitoring are outside this initial implementation.
- Git is initialized on local `main`, with `origin` configured to the provided GitHub repository.

## Review status

- Independent read-only audit completed against the adopted revised plan. Its remaining anonymous preview-payload risk is recorded above and in the README/module notes.
- User review/acceptance of the portfolio workflow and remaining gaps is pending.
