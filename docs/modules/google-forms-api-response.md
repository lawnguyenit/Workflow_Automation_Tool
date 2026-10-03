# Google Forms API response source (v1)

Status: reusable mapper, composed lead workflow, and inactive n8n polling template implemented. The poller is imported into local n8n 2.41.6 and visually verified as inactive with no credential assigned. Only synthetic/unit/local API execution evidence exists; no OAuth consent, Form API request, response data, poller execution, or activation has been performed.

## Contract

`source.google_forms_api_response` accepts exactly `{ "form_id": "...", "form": { ...Google Forms API Form... }, "response": { ...FormResponse... } }`.

- It requires the schema's `formId` to match the configured form ID and, when present, the response's `formId` to match too.
- It resolves the five configured lead fields by exact question titles in `form.items[].title`, then reads the associated `questionId` and its single `textAnswers.answers[].value` from the response. Missing answers remain empty for `lead.intake` to flag; missing/duplicate titles, malformed items, multiple values, non-text values, and over-limit strings fail closed without returning answer content.
- The source key is `<formId>:<responseId>`. This is stable across polls and independent of response-sheet sorting or row movement. PostgreSQL still deduplicates only source events, not the same person across submissions or CRM/email side effects.
- The mapper is pure Python and makes no Google calls. n8n fetches Form schema and response pages; credentials belong only in n8n's encrypted credential store.

## OAuth boundary

The polling export needs two Google OAuth scopes, both read-only and both limited to Google Forms data:

- `https://www.googleapis.com/auth/forms.body.readonly` to retrieve the form's question titles and IDs with `forms.get`.
- `https://www.googleapis.com/auth/forms.responses.readonly` to list submitted responses with `forms.responses.list`.

Google documents the distinct scope requirements for [reading the Form schema](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms/get) and [listing responses](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms.responses/list). A FormResponse includes an output-only `responseId` and timestamps ([reference](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms.responses)). These scopes do not request Drive-wide read/write access. They do allow reading Forms content/responses in the authorized Google account, so the owner must inspect and approve the OAuth consent screen in n8n. The workflow export includes no OAuth client ID, client secret, access token, or credential reference.

## n8n polling composition

`workflows/n8n/lead-management-google-forms-api.local.json` is inactive and polls every five minutes when an owner activates it. It reads Form schema and response pages using HTTP Request nodes with generic OAuth2, sends each response through the local lead workflow, and keeps source cursor state in small n8n workflow static data. The cursor advances only after all responses reach accepted storage states and only when the run mode is `MOCK` or `REAL`; `DRY_RUN` never consumes events. Static data is saved by successful production executions, not manual tests, and is suitable only for this single-instance low-volume reference. It is not a distributed lock or high-volume checkpoint store.

Response listing uses a timestamp `>=` cursor, API page size 500, at most 20 pages per run, and a 250 ms interval between page requests. The template stops before processing if the page cap is reached while another page token remains; the cursor stays unchanged. The API's page-token and timestamp-filter behavior is documented by Google. A response retried at the inclusive timestamp boundary may be mapped again; the stable response ID plus PostgreSQL unique key suppresses duplicate storage. Qualification currently defaults to manual review and has no live model credential. Do not enable paid model calls until a durable pre-qualification idempotency/cost gate is added.

The form ID in the workflow is a placeholder. In the current authenticated local n8n session, the owner still needs to configure a generic OAuth2 API credential with the two read-only scopes above, select it on both Google HTTP nodes, and replace the placeholder Form ID. The local API defaults to `DRY_RUN`. A database-writing `MOCK` run additionally needs the local API bearer key entered as an n8n HTTP credential; neither credential is committed. The separate `workflows/n8n/lead-management-google-forms-api.manual-preview.json` exercises the local API with a synthetic Form/FormResponse and no Google OAuth. Follow [the local OAuth runbook](../runbooks/google-forms-api-local-oauth.md) for the owner-operated setup.

## Privacy, failures, and limits

- The committed poller was imported into local n8n 2.41.6 using a temporary local workflow ID; the committed JSON was not changed. UI inspection shows its nine expected nodes and inactive state. No credentials are attached. Execution retention is disabled for manual, successful, and failed runs. A separate two-node manual synthetic preview was run through the Docker API in `DRY_RUN`; one earlier synthetic run remains in local n8n history. Google OAuth, polling pagination, and the imported poller's runtime remain unverified. A direct host request to the n8n REST workflows endpoint returned HTTP 401 without n8n API authentication.
- Form question-title drift or duplicate titles halt mapping. A schema/response from a different Form is rejected. Do not silently rename fields in code to accommodate a changed Form; update instance config and its tests.
- HTTP requests retry up to three times. API failures stop the execution; the cursor only changes after the whole batch has accepted storage results. A partial batch is retried from the previous cursor, so previously stored response IDs can be revisited and deduped.
- The five-minute schedule and 20-page cap are bounded for this low-volume test Form, not capacity evidence. Large backlogs, overlapping poll executions, n8n queue/worker deployment, and Google quotas have not been tested. Static cursor state is not atomic across concurrent workflow executions.
- Google Forms responses contain personal information. This pipeline stores normalized response fields in local PostgreSQL; purpose, notice/consent, retention, access, and any later model/CRM transfer must be decided before live prospect data is used. The Form remains unpublished and has no response rows.

## Verification

- Unit tests cover responseId event identity, exact title-to-question-ID mapping, missing answer handling, Form mismatch, title drift/duplication, multiple/non-text answers, answer length bounds, and sanitized API results.
- The inactive n8n export is structurally tested for read-only OAuth placeholders, bounded pagination, no saved execution data, successful-store cursor advancement, and `DRY_RUN` non-consumption.
- The committed polling export is imported in local n8n and remains inactive. A separate committed manual preview export is synthetic and `DRY_RUN`; an equivalent two-node workflow was manually assembled in n8n and executed successfully through the local API. Neither fact establishes Google API access or poller runtime.
- No Google API call or OAuth authorization has occurred. No actual Form response was read or stored.
