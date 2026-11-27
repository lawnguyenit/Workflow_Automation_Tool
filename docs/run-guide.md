# Run guide (n8n stack)

The quick version is in the [README](../README.md#run-it-in-10-minutes). This page adds what the
README leaves out. The Python-era run guide (FastAPI, execution modes) is in
[history/](history/README.md).

## Stack

`docker compose up -d` from the repo root starts two containers from `infra/docker-compose.yml`
(the root file only `include`s it): `n8nio/n8n:2.49.8` and `postgres:18-alpine`. Both ports are bound
to `127.0.0.1`. On the first start of an empty Postgres volume, every `infra/postgres/*.sql` file runs
in name order: `00-create-n8n-db.sql` (n8n's own database) and `10-invoice-intake.sql` (application
tables). Init scripts do not run again on an existing volume; apply later schema changes by hand.

Required in `.env`: `POSTGRES_PASSWORD` (URL-safe) and `N8N_ENCRYPTION_KEY` (>= 32 random characters;
back it up, without it stored credentials cannot be read). Never commit `.env`.

## Credentials (names must match exactly)

| Name in n8n | Type | Values |
| --- | --- | --- |
| `Postgres (local)` | Postgres | host `postgres`, port `5432`, database `workflow_automation`, user `workflow`, password = `POSTGRES_PASSWORD`, SSL off |
| `Gemini API` | Google Gemini(PaLM) API | your API key; host `https://generativelanguage.googleapis.com` |
| `Gmail (secondary account)` | Gmail OAuth2 API | OAuth client from a Google Cloud project you own; use a secondary mailbox, not a primary one |

Importing before the credentials exist works, but the nodes stay unattached; create the credentials and
run the import again.

## Import and publish

```bash
node scripts/n8n-import.js --docker "$(docker compose ps -q n8n)"
```

The script ends with `expected 7, found on target 7` when all workflows landed. In the n8n editor
publish, in this order: the five `sub:` workflows and `error handler`, then `invoice intake`.
n8n 2.x cannot call an unpublished sub-workflow, and publishing needs the credentials to exist.
Before publishing, replace the `example.invalid` recipients in the **Notification settings** node
(`sub: notify`) and the **Alert settings** node (`error handler`).

## Things to know before real mail

- The Gmail trigger polls once a minute and only sees mail after `invoice intake` is published.
- The PDF text is sent to Gemini. Check Google's API terms and data region for your documents.
- Failed executions keep item data in n8n's database; `EXECUTIONS_DATA_MAX_AGE` (default 336 hours)
  prunes it. Match it to your retention policy.
- Known limits L1 to L5 are in [architecture.md](architecture.md#known-limits).
- Incident handling: [runbooks/incidents.md](runbooks/incidents.md).
