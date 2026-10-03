# Implementation state

## Current verified state

- The approved framework and synthetic Intelligent Document Intake portfolio are implemented under `src/workflow_automation/`.
- Core includes shared result/execution contracts, capability/implementation/instance/executor/workflow-definition/workflow-instance models, validated registry, Python and HTTP executors, generic workflow runner, and FastAPI module/catalog/workflow routes.
- Reference modules include fixture/Gmail read ingestion, bounded PDF text extraction, deterministic/strict-schema LLM extraction, configurable validation, PostgreSQL idempotent record storage, and Slack notification with an outbox.
- The workflow API defaults to `DRY_RUN`; live modes require separate credentials/configuration. The API response removes raw attachment bytes and extracted PDF text from step results.
- Workflow definitions reference module capabilities; workflow instances bind each step to a separately configured module instance. The HTTP executor targets concrete module-instance routes.
- `MOCK`/`REAL` and live Gmail reads require an optional shared bearer key; `DRY_RUN`/`REPLAY` remain available without a key for the n8n demo. The API accepts caller-supplied preview data and does not verify replay capture/sanitization. This is not per-user authorization; keep the API private and sanitize replay data before submission.
- `fixtures/` contains synthetic-only email/PDF inputs. Docker Compose, initial PostgreSQL schema, `.env.example`, and an n8n import template are present.
- `docs/modules/portfolio-modules.md` records limits, duplicates, concurrency, retries, recovery, capacity, data handling, and live-verification gaps. `docs/releases/0.1.0.md` is the release-note draft.
- The shared read-only workflow risk researcher is defined in `../.codex/agents/workflow-module-risk-researcher.toml`.

## Evidence boundary

The unit suite, compilation, dependency sync, Compose build/startup, PostgreSQL sequential idempotency, n8n health endpoint, n8n graph checks, and local HTTP workflow runs are verified. n8n workflow import/execution and Gmail/OpenAI/Slack live paths are not. Git is initialized on `main` with `origin` configured to the user-provided remote.

## Evidence

- Repository inspection found no existing workflow automation implementation in this tool directory; `Scraping_Tool` is a separate project.
- No provider credentials or client environment were supplied; live integration testing is outside current evidence.
