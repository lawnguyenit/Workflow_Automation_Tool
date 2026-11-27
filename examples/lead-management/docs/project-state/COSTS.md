# Provider cost and account checklist

Reviewed: 2026-10-03. Prices and billing rules change; verify the customer's actual plan and the linked provider page before enabling a billable feature.

This portfolio uses synthetic fixtures, mocked HTTP transports, and local Docker/PostgreSQL. Those checks do not call paid providers. No billable provider call is required to establish the bounded module behavior documented below. See [DEC-JOB-010](DECISIONS.yaml) for the approved testing policy.

| Capability | Cost / quota posture | What a client must supply | Portfolio evidence and limit |
|---|---|---|---|
| Google Forms API (`CAP-001/002`) | Google currently states standard Forms API use has no additional charge within quotas. Quota-limit overage charges are planned for later in 2026. | Google account with access to the intended Form; client-approved OAuth scopes and protected credential. | Mapper/checkpoint tests are local/synthetic. No OAuth grant or live Form API call is configured. Check [Forms API limits and pricing](https://developers.google.com/workspace/forms/api/limits). |
| OpenAI qualification (`CAP-004`) | API usage is billed separately from ChatGPT Plus, based on model/features and tokens. | Client API organization/key, approved data-use basis and rubric, current model choice, and a client-approved spend limit. | Strict schema, fixtures, call-count ledger, and mock transport are tested; there is no USD spend enforcement or live quality/cost evidence. Check [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) and [separate ChatGPT/API billing](https://help.openai.com/en/articles/9039756-billing-settings-in-chatgpt-vs-platform). |
| GoHighLevel CRM (`CAP-006`) | Requires an active account. Optional workflow, AI, messaging, and other features may have plan- or usage-based charges; the exact bill depends on the client account/features. | Client-owned test Location, token/scopes, duplicate policy, mappings, and approval before writes. Client confirms the account billing page before enabling optional paid features. | Contact/opportunity success-cache, unknown-hold, and concurrency guards are tested with local PostgreSQL and mocked HTTP; tags have separate effect-guard coverage. No live HighLevel request or account-specific behavior is verified. Check [HighLevel Pricing & Billing](https://help.gohighlevel.com/support/solutions/articles/155000001156-highlevel-pricing-guide). |
| Gmail send (`CAP-008`) | Google currently states standard Gmail API use has no additional charge within thresholds; quotas apply and charges above the daily threshold are planned later in 2026. The mailbox/Workspace plan can have its own account cost. | Client mailbox, protected OAuth with `gmail.send`, and explicit recipient/content authorization for a live send. | Mock transport and local PostgreSQL outbox are tested; no call from this module to Gmail has been made. This does not authorize sending. Check [Gmail API quota and pricing](https://developers.google.com/workspace/gmail/api/reference/quota). |
| n8n orchestration | The standard self-hosted Community Edition is available from GitHub; hosting, storage, backups, and operations still have a cost. n8n Cloud is subscription-based. | Either customer-managed n8n hosting/credentials or an approved n8n Cloud plan, according to the job. | Local n8n 2.41.6 and Docker are used. A client's plan/edition and concurrency limits remain job-specific. Check [n8n plans](https://n8n.io/pricing/). |
| PostgreSQL / Docker runtime | The software is locally self-hosted in this portfolio; compute, disk, backup, and managed-database services may cost money in a client deployment. | Client hosting or managed database, access policy, backup/retention owner, and capacity target. | Synthetic Compose/PostgreSQL integration passed on the local Docker environment. No production load/capacity claim is made. |

## Enablement rule

1. Verify exact client plan, permissions, quotas, and feature charges before activating a paid or external step.
2. Keep paid module tests mocked unless a live call is required by client acceptance and the client supplies the account/API and authorizes the test.
3. Separate provider cost from implementation readiness: passing mocked tests means the adapter contract/failure behavior is bounded, not that every tenant, plan, OAuth review, mapping, or current provider API will work without client-specific validation.
4. Never use portfolio funds or the user's account for a paid API call without a new explicit decision.

## Official references

- [Google Forms API usage limits and pricing](https://developers.google.com/workspace/forms/api/limits)
- [Gmail API usage limits and pricing](https://developers.google.com/workspace/gmail/api/reference/quota)
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
- [ChatGPT and API billing are separate](https://help.openai.com/en/articles/9039756-billing-settings-in-chatgpt-vs-platform)
- [HighLevel Pricing & Billing](https://help.gohighlevel.com/support/solutions/articles/155000001156-highlevel-pricing-guide)
- [n8n plans and pricing](https://n8n.io/pricing/)
