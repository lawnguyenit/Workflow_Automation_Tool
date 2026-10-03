# Lead-management portfolio instance

This folder tracks the supplied Upwork brief as a bounded portfolio exercise. It is separate from reusable module source and contains no credentials, private Form IDs, or respondent data.

## Current state

- Intake is bounded; local source/module discovery is recorded.
- The pipeline design is **proposed**, not design-ready for live execution. Google OAuth branding is configured in External/Testing mode and the user accepted the separate Google User Data Policy for this private test app. Choosing whether Forms scopes apply to the current Google account or a dedicated test account remains an owner gate, along with GHL matching/pipeline rules, AI rubric/budget, and follow-up policy/event sources.
- No live Forms, GHL, LLM, or lead-follow-up operation has been executed. The test Form is private/unpublished and the n8n poller remains inactive.
- The current independent audit is [AUDIT-0.12.0.yaml](../AUDIT-0.12.0.yaml), verdict `FAILED_AUDIT` for the full brief.

## Artifacts

- [JOB_SPEC.yaml](JOB_SPEC.yaml): normalized goal, access facts, acceptance criteria, and owner unknowns.
- [DECISIONS.yaml](DECISIONS.yaml): approved choices and unresolved business/access decisions.
- [DISCOVERY.yaml](DISCOVERY.yaml): current source, runtime, module, and failure evidence.
- [PIPELINE.yaml](PIPELINE.yaml): module reuse/adaptation, composition edges, stops, and success condition.
- [TASKS.yaml](TASKS.yaml): bounded owner and workflow-lead work with gates and verification criteria.

Do not mark a blocked edge complete based on a module unit test. Re-audit the full composed path after owner inputs and runtime evidence exist.
