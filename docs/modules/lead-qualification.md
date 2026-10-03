# Lead qualification module (v1)

Status: implemented and synthetic-fixture verified; no live model request has been made.

## Contract

- Capability: `lead.qualify`; accepts a normalized `lead` and explicit `criteria` containing a rubric, named classifications, and required fields.
- Supports `REAL` OpenAI Responses API calls and explicit mock/replay fixtures. Without criteria, required fields, valid output, or configured provider, it preserves the lead and holds qualification for human review.
- Real requests use OpenAI Structured Outputs with a strict JSON Schema, `store: false`, `max_output_tokens: 500`, and at most 8,000 characters of enquiry text. The model receives the rubric, service request, and free-text description, but not separate name/email/phone/source identifiers. Free text may itself contain identifying details.
- Output includes an allow-listed class, short summary, confidence, missing information, stable reason codes, and prompt version `lead_qualification_v1`. Classes not present in the approved rubric are rejected. Low-confidence, incomplete, conflicting, or unclear results remain for human review. Exact name/email/phone strings found in the contact record are redacted from the returned summary; other personal details present only in free text cannot be reliably detected or removed.
- The summary/classification is returned by internal workflow steps, but the public workflow API removes the lead's name/email/phone and enquiry text from intermediate responses.

## Safety and operational behavior

- The workflow owner must supply the rubric and classes; the module does not invent sales policy. Criteria and prompt versions should be reviewed whenever business rules change.
- A separate OpenAI API credential is required for `REAL`; a ChatGPT Plus subscription is not an API credential. No API key or model call is configured in the current instance.
- Input and output sizes are bounded. The request sends free-text enquiry content to the configured provider, so confirm purpose, notice, provider settings, and data-handling requirements before real lead data is used.
- Timeout, transport failure, 429/server errors, or invalid provider response become a review outcome. The module performs no automatic retry because retrying may incur another model call and charge.
- **Current limit:** model calls do not yet have a durable cross-process idempotency ledger, per-run spend ceiling, or response cache. Replaying an event can make another billable model call. Keep the module in synthetic/test mode until the caller's retry/reconciliation design or a future durable ledger prevents unintended duplicate calls.
- This module does not measure token usage/cost from the provider response. `max_output_tokens` and input-length cap only bound request work; they are not a spend guarantee.

## Evidence and references

- Synthetic tests verify explicit rubric/class constraints, low-confidence review, missing required fields, strict request shape, PII minimization for separate contact fields, output validation, and ambiguous provider failure behavior.
- No live model quality, cost, retention, or account billing behavior is verified.
- OpenAI's official documentation describes strict JSON Schema output for the Responses API: [Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs). The user's API organization must set its own data and retention controls.
