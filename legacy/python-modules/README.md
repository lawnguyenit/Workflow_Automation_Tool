# Legacy Python modules (not in the runtime flow)

The Python package `workflow_automation` (FastAPI module API, contracts, executors,
input/processing/storage/output/CRM modules) used to be the main code of this repo.
The product is now the n8n workflows in `../../workflows/`; the invoice rules that
mattered (validation, normalization, duplicate key) were ported to JavaScript in
`../../code/` and run inside n8n Code nodes.

This folder is kept unchanged so the earlier work can still be run and its tests
still pass:

```
cd legacy/python-modules
uv run --extra dev pytest -q
```

Only path constants in three tests were changed so they find the lead-management
exports now in `../../examples/lead-management/workflows/`.

- `infra/postgres/init.sql` is the old full schema (invoices, leads, ledgers,
  outbox, incidents). `migrations/003` and `007` belong to it. The main
  `infra/postgres/10-invoice-intake.sql` of the repo no longer contains these tables.
- `workflows/n8n/intelligent-document-intake.demo.json` is the old n8n demo made of
  HTTP nodes that call this API in `DRY_RUN` at `http://api:8000`. The current
  compose stack has no `api` service, so it only works if you run the API yourself.
- `fixtures/` is a copy of `../../tests/fixtures/` so the package keeps finding its
  default fixture folder without code changes.
- `tests/integration/` scripts need Docker Compose and the old schema; they were
  not re-run after the move.
