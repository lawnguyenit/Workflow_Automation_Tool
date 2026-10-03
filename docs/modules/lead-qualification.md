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
- REAL calls require stable source event identity, migration `004-lead-qualification-ledger.sql`, an available PostgreSQL ledger, and explicit `LEAD_LLM_MAX_CALLS_PER_DAY`. If any are missing, the module holds for review without calling the provider.
- The ledger serializes a source event across workers, stores the input/rubric/model/prompt fingerprints, and caches the validated, contact-redacted result. A replay with the same identity and fingerprints returns the cached result without another request. Changed input or criteria under the same source event is held for review.
- A durable `IN_PROGRESS` or `UNKNOWN` row blocks automatic retries after worker interruption, timeout, transport failure, 429/server error, or invalid provider response. An operator must reconcile whether the request was sent before resolving the row; do not clear it based only on the n8n error.
- The configured limit is a maximum number of attempted calls per UTC day per model. Reservations are atomic across API workers, and uncertain attempts still consume the cap. This is a call-count guard, not a monetary spend ceiling: provider usage and current model prices are not collected or used to enforce a USD amount. Set both the local daily request cap and provider account budget before any live use.
- `max_output_tokens` and input-length limits bound request size, but cannot by themselves guarantee provider cost. Changing the rubric/prompt/model for a previously processed event changes the fingerprint and is intentionally not an automatic requalification; resolve or version it under a controlled owner process.

## Evidence and references

- Synthetic tests verify explicit rubric/class constraints, low-confidence review, missing required fields, strict request shape, PII minimization for separate contact fields, output validation, replay cache behavior, changed-payload hold, and fail-closed missing-ledger configuration. A separate Compose PostgreSQL integration verifies same-event cache, concurrent claim, unknown-outcome hold, and daily-call cap behavior with a synthetic callback.
- No live model quality, cost, retention, or account billing behavior is verified.
- OpenAI's official documentation describes strict JSON Schema output for the Responses API: [Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs). The user's API organization must set its own data and retention controls.
