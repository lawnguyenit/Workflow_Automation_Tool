# User intent

## Confirmed

- The user wants a separate reusable tool for workflow automation modules.
- The catalog should cover input, processing, storage, and output, with infrastructure and operational reliability concerns documented across those stages.
- Most incoming jobs are expected to use n8n; other platforms may be used when a real job requires them.
- Keep the framework contracts platform-neutral, with n8n as the primary reference runtime and Python/HTTP/API execution where appropriate.
- Build an initial production-style portfolio workflow before a paid job: Intelligent Document Intake using Gmail/email ingestion, document extraction, structured LLM extraction, validation, PostgreSQL persistence, and notification.
- Use fixtures/mock/dry-run/replay modes to work safely without provider credentials, while marking live integration verification separately.
- Carefully surface concerns such as duplicate processing, concurrent database writes, overload, retry behavior, and debugging/operability.
- Leave enough bounded, sanitized diagnostic information to debug workflows when the user has no direct system access or only receives workflow nodes/configuration from the client.
- Describe modules in Git updates so maintainers can see their contents, guarantees, and common user concerns.
- For any real job that requires a capability not yet in the reusable catalog, complete the client work and then create a generalized reusable module in the appropriate tool project before closing the job; do not leave the capability as a client-only one-off.
- Build a concrete lead-management portfolio instance from the supplied GHL job brief and take it beyond a mock by running authorized tests against the user's own available service accounts. Add reusable modules to the framework where the instance exposes a capability gap. Keep credentials out of chat/source; distinguish synthetic tests, local integration, authorized live-account runs, and user acceptance.
- A shared read-only research agent at `../.codex/agents/workflow-module-risk-researcher.toml` checks bounded concerns against current online sources and reports gaps; the established workflow remains unchanged.

## Current account inventory for the lead-management reference

- The user has Gmail and Outlook accounts with two-factor authentication enabled.
- The user has not identified an existing CRM or GoHighLevel account and asked what CRM/GHL means; explain it in plain language before requesting any account.
- There was no existing lead form; the user authorized creating one. A private Google Form draft named `Lead Intake Lab - Test Only` and its linked response spreadsheet now exist. The form is unpublished and has zero responses. It is explicitly labeled for synthetic data and not public lead collection.
- The user has no known public HTTPS endpoint. VPS `103.178.234.213:2786` hosts an online game; do not connect to, probe, reconfigure, or deploy on it.
- The user has ChatGPT Plus but no OpenAI API account/key. Keep paid API calls disabled unless the user later provisions a separate API credential directly in a secure UI.
- The user permitted one test email only to an address they control and affecting no one else. On 2026-10-03, one plain-text test was sent through the connected Gmail app to `lawnguyenit@gmail.com` and verified in SENT. Do not send third-party messages under this permission.
- Use synthetic-only data for current local implementation. Do not publish the form or expose a public callback/tunnel without separate explicit instruction. Do not run live prospect data until purpose, audience/jurisdictions, notice/consent, provider/hosting, retention, and follow-up purpose/content have been established.

## Assumptions to revisit

- The tool directory name is `Workflow_Automation_Tool`.
- No live Gmail/OpenAI/LLM/PostgreSQL or client environment credentials were supplied; local implementation should support representative fixtures and document live-provider gaps.
- The portfolio is a synthetic reference system, not a real client deployment or production verification.
