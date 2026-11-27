# Lead management (example, never run against live services)

This folder keeps an earlier lead-management reference: five n8n workflow exports,
its PostgreSQL migrations and its project-state records. It is **not** part of the
invoice-intake product in `workflows/`.

- None of these workflows has run against a live Google Form, Google Forms API,
  HighLevel (GHL) CRM, LLM provider or Gmail sender. One manual `DRY_RUN` preview
  was executed once in a local n8n against the legacy Python API with synthetic data
  (recorded in `docs/project-state/`, not re-run after the move).
- The HTTP nodes call the legacy Python API in `legacy/python-modules/`; they do not
  work on their own.
- `infra/postgres/migrations/` holds the lead-specific tables (leads, qualification
  ledger, follow-up suppression, source-poll checkpoint, GHL ledgers). They are not
  applied by the main `infra/postgres/init.sql`.

Layout:

```
workflows/                    5 n8n exports (inactive, credential-free placeholders)
infra/postgres/migrations/    002, 004, 005, 006, 008, 009 (lead-specific)
docs/project-state/           job spec, discovery, decisions, pipeline, tasks, costs, status map
```

Moved here on 2026-11-27 from `workflows/n8n/` and `docs/project-state/lead-management/`.
The pre-move tree is preserved in branch `snapshot/pre-n8n-refactor-2026-11`.
