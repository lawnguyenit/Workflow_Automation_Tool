# Google Form response row module (v1)

Status: local account schema inspected; module and composed workflow are fixture/synthetic verified. No Google API call, response submission, live n8n trigger, or prospect-data processing has occurred.

## Source evidence and contract

- The connected Google Drive account contains a private response spreadsheet for `Lead Intake Lab - Test Only`. Its only visible tab is `Câu trả lời biểu mẫu 1`; row 1 contains six headers and row 2 is empty. The account-specific spreadsheet/tab IDs are stored only in the ignored local `.env`.
- Header values observed in the owned response sheet: `Dấu thời gian`, `Tên đầy đủ`, `Email address`, `Phone (optional)`, `Service requested`, and `Describe your request`.
- `source.google_form_response` accepts exactly `{ "row_number": 2, "row": { ...header-keyed values... }, "change_type": "added" }`. The mapper independently rejects any `change_type` other than `added`, even though the n8n graph also filters updates. It uses exact configured headers, ignores unconfigured columns such as timestamp/change metadata, and emits the canonical `lead` object for `lead.intake`.
- Event identity is `<spreadsheet id>:<tab id>:row:<row number>` and account scope is a stable configured alias. Retries for the same row therefore reach `lead.store` with the same idempotency key. Do not sort, delete, or insert rows in the response tab; row-number identity assumes append-only form responses.
- The mapper does not contact Google. n8n owns OAuth and polling via its native Google Sheets Trigger. Do not paste OAuth tokens into the API, workflow JSON, or Git.

## n8n composition

`workflows/n8n/lead-management-google-form.local.json` uses the Google Sheets Trigger's **Row Added or Updated** event, then filters `change_type == "added"`. The n8n `Row Added` event does not expose a row number; source inspection of the installed n8n 2.41.6 implementation showed that `anyUpdate` produces `row_number` and `change_type` fields. This event reads revision history and the node reports that edit access is required. Its bundled OAuth scopes include full `drive`, `drive.file`, `spreadsheets`, and `drive.metadata` access. Review that account-wide access before connecting it; recheck node behavior and scope changes when upgrading n8n. The trigger's initial poll establishes a baseline and does not backfill pre-existing responses; this is appropriate only while the private test Sheet has zero response rows.

The source workflow is inactive, has no embedded Google or API credential, uses spreadsheet/tab placeholders, and calls the local API in `DRY_RUN`. This prevents DB writes by default. A separate `workflows/n8n/lead-management-google-form.manual-preview.json` contains one synthetic row and no Google credential, so the user can test the local n8n-to-API path without connecting Google. To persist a test response, an operator must configure the user's Google OAuth credential and private sheet IDs in n8n, create a dedicated n8n HTTP Header credential for the local API, then deliberately change the API run context to `MOCK`. Keep the API on loopback/private Docker networking. No public webhook or tunnel is required for this source.

### OAuth and identity design gate

Do not connect the Google Sheets Trigger to the owner's main Drive account yet. For this node/version, the `Row Added or Updated` mode needs Sheet edit permission and the bundled credential requests broad Drive/Sheets scopes. Official Google Forms API `forms.responses.list` also exposes an alternative: read responses with the `forms.responses.readonly` scope, and each response has a response ID that can serve as the idempotency identity. Before any live OAuth, prefer evaluating a Forms API polling adapter with response-ID persistence; it still needs owner-authorized OAuth and has not been implemented or verified here. The narrower scope is read-only for Forms responses, but it can read responses from the account's forms, so consent remains a real account-level decision.

## Failure and privacy boundaries

- Header changes fail closed with `FORM_HEADERS_CHANGED`; fix the instance mapping instead of silently shifting fields.
- Blank fields are passed as empty strings so `lead.intake` can flag them for human review. Non-text mapped cell values fail closed.
- Only rows marked added are accepted into the lead graph. Updated rows are rejected by the n8n filter and again by the mapper; updates cannot overwrite or reprocess an already stored lead through this composition.
- Direct mapper and composed workflow API responses replace the mapped lead with a minimal status. The internal workflow still handles contact fields, and n8n/provider execution retention remains the operator's responsibility. The included n8n export disables saved success/error data.
- The source spreadsheet is unpublished and currently has no data rows. OAuth setup, an actual trigger event, row-number output, and an end-to-end Google-to-PostgreSQL run remain unverified. Do not activate it on real prospects until purpose, notice/consent, retention, recipient, and follow-up policy are decided.

## Verification

- Unit tests cover exact observed header mapping, stable source event keys, missing values, header drift, malformed row identity/value, rejection of non-added events, API PII redaction, the trigger export's added-row filter and inactive defaults, and the manual preview's synthetic/non-mutating settings.
- `tests/integration/google_form_lead_postgres.py` uses synthetic row values and a synthetic sheet scope against local Compose PostgreSQL. It verifies first insert, repeated-row duplicate suppression, changed-payload conflict, and cleanup. It does not contact Google or invoke n8n.

## Official references

- [n8n Google Sheets Trigger](https://docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.googlesheetstrigger/)
- [Google Sheets API](https://developers.google.com/sheets/api)
- [Google Forms API `forms.responses.list` and OAuth scopes](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms.responses/list)
- [Google Forms `FormResponse` fields, including `responseId`](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms.responses)
