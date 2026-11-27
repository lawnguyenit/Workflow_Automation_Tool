# Google Forms API response source (v1)

Status: reusable mapper, composed lead workflow, and inactive n8n polling template implemented. The revised 18-node poller is imported into local n8n 2.41.6, exported back, and verified inactive with no credential assigned. OAuth branding is configured in Google Cloud in External/Testing mode with only the owner's account listed as a test user. No Forms scope, OAuth client/grant, Form API request, response data, poller execution, or activation exists; provider behavior remains unverified.

## Contract

## Cost and client account

- Google currently lists standard Forms API use as no additional cost, with per-minute quotas; Google says quota-limit overage charges are planned for later in 2026. Check the live [Forms API usage and pricing page](https://developers.google.com/workspace/forms/api/limits) before enabling a client's poller.
- OAuth, access to the actual Form, and Google account/Workspace terms still apply. The account owner must approve the account-level read scopes. Synthetic mapper/checkpoint tests require no Google API request; this does not verify OAuth access or live Form schema.

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

## Owner test Form discovery — 2026-10-03

The connected Drive account's folder listing exposed the private `Lead Intake Lab - Test Only` Google Form and its linked response Sheet. The Form was opened in the owner's signed-in browser for read-only inspection. Its five question titles match the mapper configuration exactly: `Tên đầy đủ`, `Email address`, `Phone (optional)`, `Service requested`, and `Describe your request`. The Form remains unpublished and has no submitted responses. The private Form ID is intentionally omitted from Git and still needs to be entered in the local n8n workflow. Browser/UI title inspection is not evidence of Forms API OAuth access or live `questionId` schema; those remain unverified until the Forms API credential is connected. Current-state correction (2026-10-03): a dedicated Google Cloud project has Forms API enabled. After the user authorized accepting the separate Google API Services User Data Policy for this private test app, OAuth branding was created in External/Testing mode with the owner's account as the sole test user. The Data Access page remains empty; no Forms scope, OAuth client, grant, API request, or response read exists. The owner must choose whether to use the current Google account (the two read-only scopes cover every Form it can access) or a dedicated test account with only the private test Form shared to it.

## n8n polling composition

`workflows/n8n/lead-management-google-forms-api.local.json` is inactive and polls every five minutes when an owner activates it. It reads Form schema and response pages using HTTP Request nodes with generic OAuth2, sends each response through the local lead workflow, and uses `state.source_poll_checkpoint` for the timestamp cursor and per-Form lease. The workflow acquires before provider reads, renews after response-page retrieval, releases on an empty poll, and commits only after all responses reach accepted storage states. `DRY_RUN` performs no checkpoint writes; `MOCK`/`REAL` use PostgreSQL and require the local API bearer credential when the API is configured to enforce it. The committed workflow does not contain this credential. The lease is a per-source serialization/fencing mechanism, not a distributed exactly-once boundary for downstream provider effects.

Response listing uses a timestamp `>=` cursor, API page size 500, at most 20 pages per run, and a 250 ms interval between page requests. The template stops before processing if the page cap is reached while another page token remains; the cursor stays unchanged. The API's page-token and timestamp-filter behavior is documented by Google. A response retried at the inclusive timestamp boundary may be mapped again; the stable response ID plus PostgreSQL unique key suppresses duplicate storage. Qualification currently defaults to manual review and has no live model credential. Do not enable paid model calls until a durable pre-qualification idempotency/cost gate is added.

The Form ID in the committed workflow is a placeholder. The private test Form was located in the connected Drive account; its ID must be entered only in the local n8n workflow. The owner still needs to configure a generic OAuth2 API credential with the two read-only scopes above and select it on both Google HTTP nodes. The local API defaults to `DRY_RUN`. A database-writing `MOCK` run additionally needs the local API bearer key entered as an n8n HTTP credential; neither credential is committed. The separate `workflows/n8n/lead-management-google-forms-api.manual-preview.json` exercises the local API with a synthetic Form/FormResponse and no Google OAuth. Follow [the local OAuth runbook](../runbooks/google-forms-api-local-oauth.md) for the owner-operated setup.

## Privacy, failures, and limits

- The revised 18-node poller was imported into the existing local n8n workflow ID using n8n's CLI, then exported back. Structural inspection confirmed the expected checkpoint nodes, `active=false`, no credentials attached, and no static workflow cursor. The committed JSON still contains no private Form ID or OAuth/API secret. Execution retention is disabled for manual, successful, and failed runs. A separate committed manual synthetic preview had one successful n8n UI execution to the local API in `DRY_RUN`; it did not contact Google. Google OAuth, polling pagination at runtime, checkpoint call wiring under real n8n execution, and the poller's end-to-end runtime remain unverified. A direct host request to the n8n REST workflows endpoint returned HTTP 401 without n8n API authentication.
- Form question-title drift or duplicate titles halt mapping. A schema/response from a different Form is rejected. Do not silently rename fields in code to accommodate a changed Form; update instance config and its tests.
- HTTP requests retry up to three times. API failures stop the execution; the cursor only changes after the whole batch has accepted storage results. A partial batch is retried from the previous cursor, so previously stored response IDs can be revisited and deduped.
- The five-minute schedule and 20-page cap are bounded for this low-volume test Form, not capacity evidence. Large backlogs, n8n queue/worker deployment, and Google quotas have not been tested. A 900-second lease can expire during a long processing batch; stale cursor commits fail closed, while downstream modules remain responsible for duplicate side-effect safety.
- Google Forms responses contain personal information. This pipeline stores normalized response fields in local PostgreSQL; purpose, notice/consent, retention, access, and any later model/CRM transfer must be decided before live prospect data is used. The Form remains unpublished and has no response rows.

## Verification

- Unit tests cover responseId event identity, exact title-to-question-ID mapping, missing answer handling, Form mismatch, title drift/duplication, multiple/non-text answers, answer length bounds, and sanitized API results.
- Re-ran `tests/integration/google_forms_api_lead_postgres.py` inside the Compose API container with synthetic data: mapping succeeded; storage returned `STORED`, same-event retry returned `DUPLICATE`, and changed payload returned `PAYLOAD_CONFLICT_REVIEW`. The harness cleaned its synthetic event and a follow-up count returned zero. Google Forms API was not called.
- The inactive n8n export is structurally tested for read-only OAuth placeholders, bounded pagination with a fail-closed page-cap check, no saved execution data, lease acquisition/renewal/release, successful-store checkpoint commit, and `DRY_RUN` non-consumption.
- The revised committed polling export is imported in local n8n and remains inactive with no credentials attached. The separate committed manual preview export is imported and has one successful n8n UI execution to the local API using synthetic `DRY_RUN` data. Neither fact establishes Google API access or poller runtime.
- No Google API call or OAuth authorization has occurred. No actual Form response was read or stored.
