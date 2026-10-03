# Workflow Automation Framework — Implementation Plan

**Status:** Initial document-intake reference is implemented; the lead-management reference instance is approved and moving through bounded local/live-account discovery (2026-10-03).
**Purpose:** Build a reusable automation framework first, then use the same framework to create concrete, production-style workflow instances for portfolio and future freelance jobs.

## Execution tracker

| Stage | State | Evidence / next action |
|---|---|---|
| 1. Freeze minimal core model | Implemented and unit-verified | Contracts, definitions, implementations, executors, module instances, reusable workflow definitions, concrete workflow instances, validated graph, and generic runner are present. |
| 2. Implement Gmail/email, document extraction, PostgreSQL | Implemented; fixture/mocks verified | Gmail read adapter, bounded PDF parser, source-key PostgreSQL store and schema. One self-addressed Gmail app test was verified; local Gmail API module and Gmail read adapter have not been live-tested. |
| 3. End-to-end portfolio workflow | Implemented; local dry-run verified | LLM extraction, validation, PostgreSQL preview, outbox/Slack output and API/n8n entry point are connected. |
| 4. Safe-debug infrastructure | Implemented; local behavior verified | Synthetic fixtures, mocked provider tests, DRY_RUN/MOCK/REPLAY/REAL modes, correlation context, sanitized errors/API response, and recovery statuses. |
| 5. Reliability hardening | Implemented partially; scoped checks verified | Source-key conflict behavior, conditional outbox claim, timeout ambiguity, limits, and mock failure cases are covered. Live database races, capacity/load, provider rate limits, queues/backpressure, and production observability remain untested or not implemented. |
| 6–7. Demand-driven catalog and job binding | Active; GHL, Gmail, Sheets-row, and Google Forms API adapters exist; provider access remains account-bound | Gmail outbox and both synthetic Google Form source paths have local Docker PostgreSQL integration. Selected Forms API workflow still needs owner OAuth, live schema verification, and n8n import/runtime. GHL, local-runtime Gmail OAuth, LLM, and owner business-rule bindings remain unavailable. |
| 8. Lead-management portfolio instance | Forms API composition implemented locally; reusable suppression gate implemented; supplied Upwork scope remains partially implemented | Local API + Docker PostgreSQL map a synthetic Forms API response, normalize, hold qualification for review, and dedupe source events. The Forms API polling workflow is inactive, credential-free, and unimported; cursor is single-instance static data, not a distributed checkpoint. GHL, live model, sender OAuth, reply/booking/opt-out event wiring, follow-up composition, alerts/recovery, and live end-to-end acceptance remain open. |

### Lead-management intake snapshot

| Item | State | Evidence / owner / next step |
|---|---|---|
| Business outcome and initial scope | Confirmed | Supplied Upwork brief: capture one enquiry source, qualify, upsert GHL contact/opportunity, send approved follow-up, stop on reply/booking/opt-out/pause. |
| Build a concrete instance and reuse new capabilities | Confirmed | User explicitly asked to start, finish, and test on available real accounts; framework gaps exposed by the instance become reusable modules. |
| Local lead intake modules | Implemented; unit and Docker PostgreSQL API integration verified | `lead.intake` and `lead.store`, release `0.2.0`; first insert, identical duplicate, changed-payload conflict, missing-field review and response redaction were observed with synthetic data. |
| Follow-up suppression | Reusable module implemented; unit and local PostgreSQL integration verified | `lead.followup_gate`, release `0.10.0`; durable fail-closed eligibility and suppression events. It is not yet connected to event sources, an approved follow-up sequence, or a sender. |
| CRM account and test location | No CRM/GHL account identified; blocks GHL live run | User does not know what CRM/GHL is and has not said they own an account. Generic contact, additive tags, and opportunity adapters are implemented with synthetic HTTP tests; live binding still needs a user-owned Location, private integration scopes, contact/custom-field/tag/pipeline/stage mapping, and duplicate policy. Do not create third-party CRM data. |
| Input source and public callback reachability | Test-only Google Form and linked response Sheet exist; form remains unpublished; user selected Forms API read-only | `source.google_forms_api_response` maps exact form titles/question IDs and stable `responseId`; its inactive poll template requests schema and responses with read-only scopes. OAuth consent, exact live schema, import, and runtime remain unverified. The previous Sheets adapter is optional. Local n8n/API bind to loopback only. User has no public HTTPS endpoint; listed VPS hosts an online game and must remain untouched. Do not publish or tunnel the form without a separate explicit instruction. |
| Sender account | Gmail and Outlook available; 2FA enabled; local sender credential not configured | The one authorized self-addressed test was sent through the connected Gmail app and verified in Sent. It did not use the local Gmail module or n8n. No more messages are authorized. |
| Qualification criteria, fields, pipeline/stage, and message/stop behavior | Material business choices unresolved | Do not invent sales criteria or marketing. Use a fail-closed `REVIEW_REQUIRED`/manual-review default; create no automated nurture until the owner defines/approves criteria, exact content and stop rules. |
| LLM provider | No OpenAI API account/key; ChatGPT Plus only | Keep live LLM calls disabled. Use deterministic extraction/required-field checks and mark uncertainty for review; do not claim Plus includes API usage. |
| Policy and data-handling facts | Unresolved for production leads | First test is synthetic-only, with no third-party contacts. Before real prospects, establish source notice/consent, jurisdictions, retention, recipients, provider terms/regions, and whether follow-up is transactional or marketing. |

Current readiness: **`LOCAL_SYNTHETIC_REFERENCE_IMPLEMENTED`; Forms API mapping and workflow graph are configured locally; live Forms access remains `BLOCKED_BY_OAUTH/IMPORT`, and GHL/LLM remain `BLOCKED_BY_ACCOUNT`.** Prior synthetic lead rows remain in the local test database; integration runs clean up their own rows. One self-addressed Gmail app test was sent; no lead-workflow email or CRM side effect has happened. A connected Drive read found the linked response Sheet has only its header row. The private Form remains unpublished with no submitted response. The current browser session is authenticated to n8n Assistant, but neither Forms workflow has been imported or run; direct REST calls from the host return 401 without API authentication. Do not touch the game VPS, publish the form, expose a public callback, or use real prospects without the required access and data-handling decisions.

---

## 1. Intended outcome

Create a reusable, platform-neutral framework for composing automation systems.

The framework must separate:

1. **What a module does** — capability / module definition.
2. **How it is implemented** — concrete implementation.
3. **Who/what executes it** — runtime / executor.
4. **How it is configured for a real use case** — module instance.
5. **How multiple instances are composed** — workflow definition / workflow instance.

n8n will be the primary reference orchestration runtime because it is common in current freelance automation work, but the framework must not depend on n8n-specific semantics.

Python, HTTP/API services, queues, databases, or other runtimes may implement the same capability when appropriate.

The framework must support two simultaneous goals:

- **Reusable engineering assets:** reduce repeated implementation work across future jobs.
- **Concrete portfolio systems:** prove that the framework can produce real, observable, testable workflow instances.

---

## 2. Core architecture

```text
                     FRAMEWORK
                         │
          ┌──────────────┴──────────────┐
          │                             │
      CONTRACTS                     RUNTIMES
          │                             │
          ↓                             ↓
  MODULE DEFINITIONS               EXECUTORS
          │                             │
          └──────────────┬──────────────┘
                         ↓
                IMPLEMENTATIONS
                         ↓
                    INSTANCES
                         ↓
                WORKFLOW GRAPH
                         ↓
                RUN / OBSERVE
```

The framework must explicitly model five major layers.

---

## 3. Layer 1 — Module Contract

`ModuleContract` defines the common behavioral and operational expectations shared by modules.

It does **not** perform work.

Minimum contract areas:

### Identity and compatibility

- Module name
- Contract/schema version
- Implementation version
- Runtime compatibility
- Provider/API compatibility where applicable

### Input/output

- Typed schema or explicit shape
- Item / batch / binary semantics
- Required and optional fields
- Size or count limits
- Validation failures

### Execution context

- `workflow_id`
- `run_id`
- `correlation_id`
- `attempt`
- timestamps
- safe provenance references

### Result model

A module must return an explicit result state such as:

```text
SUCCESS
PARTIAL
FAILURE
REVIEW_REQUIRED
```

The result must distinguish:

- business result
- operational status
- structured error
- retryability
- output reference or bounded output payload

### Safety and side effects

Where relevant, modules must declare:

- idempotency scope
- duplicate policy
- side-effect boundary
- retry safety
- timeout behavior
- rollback or compensation assumptions

### Operations

Where relevant:

- concurrency assumptions
- rate/capacity limits
- backpressure behavior
- checkpoint/recovery behavior
- telemetry
- log redaction requirements

The common contract must **not** assume that every module is synchronous, item-based, stateless, or safe to retry.

---

## 4. Layer 2 — Module Definition

`ModuleDefinition` describes **WHAT capability is required**.

It is platform-neutral and contains no provider-specific execution logic.

Example:

```yaml
module: document.extract
version: 1

purpose:
  Extract usable content and structured fields from a document.

input:
  document_ref: string

output:
  text: string
  fields: object
  confidence: number

possible_results:
  - SUCCESS
  - PARTIAL
  - FAILURE
  - REVIEW_REQUIRED
```

Examples of module definitions:

```text
email.ingest
document.extract
llm.structured_extract
data.validate
data.normalize
storage.record
crm.upsert_contact
notify.send
calendar.create_event
payment.create_invoice
```

A module definition answers:

> What operation does the workflow require?

It does **not** answer:

> Which provider, runtime, node, API, or library performs it?

---

## 5. Layer 3 — Module Implementation

`ModuleImplementation` describes **HOW a module definition is realized**.

One capability may have multiple implementations.

Example:

```text
document.extract
├── document.extract.openai_vision
├── document.extract.google_vision
├── document.extract.aws_textract
└── document.extract.tesseract
```

Example implementation metadata:

```yaml
implementation: document.extract.openai_vision
implements: document.extract

runtime: python
executor: OpenAIVisionExecutor

provider:
  name: openai

supports:
  - pdf
  - png
  - jpg

limitations:
  max_file_size: provider-dependent
```

Another implementation may use n8n:

```yaml
implementation: document.extract.n8n_openai
implements: document.extract

runtime: n8n

executor:
  workflow_ref: document_extract_v1
```

Implementation-specific constraints must not be silently promoted into the universal module definition.

---

## 6. Layer 4 — Executor / Runtime

`Executor` is the component that actually invokes an implementation.

It answers:

> What actually executes this implementation?

Initial executor families:

```text
N8nExecutor
PythonExecutor
HttpApiExecutor
DatabaseExecutor
```

Possible later executors:

```text
QueueWorkerExecutor
ServerlessExecutor
ContainerExecutor
BrowserAutomationExecutor
```

An executor is responsible only for runtime-specific concerns such as:

- invocation
- runtime configuration
- credential references
- timeout enforcement
- collecting runtime result
- normalizing runtime errors into the module contract
- emitting execution telemetry

Business semantics must remain in the module definition or implementation, not in the generic executor.

---

## 7. Layer 5 — Module Instance

`ModuleInstance` binds:

```text
Module Definition
+
Implementation
+
Runtime / Executor
+
Configuration
```

into something usable by a workflow.

Example:

```yaml
instance: invoice_document_extractor

module: document.extract
implementation: document.extract.openai_vision

config:
  timeout_seconds: 30
  max_retries: 3
  output_schema: invoice_v1
```

Difference:

```text
DocumentExtract
= capability

OpenAIVisionDocumentExtract
= implementation

PythonExecutor
= runtime executor

invoice_document_extractor
= configured instance
```

Module instances may vary without changing the capability.

Example:

```text
invoice_document_extractor
resume_document_extractor
contract_document_extractor
```

All may use the same underlying implementation with different schemas and operational settings.

---

## 8. Workflow Definition and Workflow Instance

A `WorkflowDefinition` describes the graph and business flow.

Example:

```text
EmailIngest
    ↓
DocumentExtract
    ↓
LLMStructuredExtract
    ↓
Validate
    ↓
Store
    ↓
Notify
```

A `WorkflowInstance` binds each step to concrete module instances.

Example:

```text
company_gmail_ingest
        ↓
invoice_document_extractor
        ↓
invoice_llm_parser
        ↓
invoice_validator
        ↓
invoice_postgres_store
        ↓
finance_slack_notifier
```

The workflow definition is reusable.

The workflow instance is concrete and deployable.

---

## 9. Module states

Every catalog entry must distinguish lifecycle state.

Recommended states:

```text
PROPOSED
SPECIFIED
IMPLEMENTED
LOCALLY_VERIFIED
STAGING_VERIFIED
CLIENT_VERIFIED
ACCEPTED
DEPRECATED
```

Never imply production compatibility from a specification alone.

Example:

```text
document.extract

Specification:        YES
OpenAI implementation: YES
n8n implementation:   YES
Local verification:   YES
Staging verification: YES
Client verification:  NO
```

---

## 10. Cross-cutting reliability layer

Reliability must not be hidden inside individual happy-path workflows.

The framework must provide reusable support for:

```text
logging
correlation IDs
retry
timeouts
idempotency
duplicate detection
replay
dry-run
mock execution
failure injection
audit trail
secret handling
redaction
state/checkpoints
monitoring
```

These concerns may span multiple module stages.

---

## 11. Test, Mock, Replay, and Safe Debugging

This is a first-class framework capability.

The system should make it possible to reproduce problems without repeatedly touching production.

Minimum structure:

```text
tests/
  fixtures/
  payloads/
  mocks/
  failure_cases/
  replay_cases/
```

Example document fixtures:

```text
invoice_normal.pdf
invoice_rotated.pdf
invoice_corrupt.pdf
invoice_large.pdf
invoice_low_quality.pdf
```

Example email payloads:

```text
normal_email.json
missing_attachment.json
duplicate_message.json
malformed_attachment.json
```

Modules should support, where practical:

```text
REAL
MOCK
DRY_RUN
REPLAY
```

### REAL

Perform real side effects.

### MOCK

Replace external services with deterministic test responses.

### DRY_RUN

Run validation, transformation, routing, and logging without committing external side effects.

Implementation note: the API defaults to `DRY_RUN`, and it does not enforce fixture-only request bodies. Callers may submit real business data even though this mode skips database writes and provider outputs. Likewise, the API does not verify that `REPLAY` inputs were captured or sanitized; sanitization is the caller's responsibility. Keep the service private and protect preview/replay execution data.

### REPLAY

Use previously captured and sanitized evidence. The current API does not validate that replay input meets this requirement; the caller is responsible for sanitization.

Example debugging flow:

```text
Production failure
        ↓
capture bounded sanitized evidence
        ↓
replay locally / staging
        ↓
identify failing module
        ↓
patch implementation
        ↓
run regression fixtures
        ↓
deploy safely
```

Direct production experimentation should not be the default debugging strategy.

---

## 12. Initial module taxonomy

### Input / ingestion

```text
webhook.ingest
email.ingest
gmail.ingest
drive.ingest
dropbox.ingest
form.ingest
```

### Processing

```text
data.validate
data.normalize
document.extract
document.parse
llm.structured_extract
llm.classify
deduplicate
business_rule.evaluate
```

### Storage / state

```text
postgres.insert
postgres.upsert
supabase.upsert
sheet.append
state.read
state.write
```

### Output / side effect

```text
notify.email
notify.slack
notify.telegram
crm.upsert
calendar.create
drive.store
dropbox.store
```

### Cross-cutting

```text
retry
timeout
idempotency
logging
audit
replay
mock
dry_run
monitoring
secret_management
```

This taxonomy is provisional and must evolve from real implementations.

---

## 13. Reference implementation strategy

Do **not** wait for a paid job before implementing everything.

The framework core remains platform-neutral, but selected capabilities must receive reference implementations.

Initial reference stack:

```text
n8n
Python
PostgreSQL
Docker
HTTP APIs
OpenAI / Claude-compatible LLM integration
```

Reference implementations prove that abstractions are usable.

A future job may replace any implementation without changing the capability model.

Example:

```diff
document.extract.openai_vision
→ document.extract.aws_textract
```

or:

```diff
runtime: n8n
→ runtime: python
```

The workflow capability graph should require minimal change.

---

## 14. First concrete module implementations

Implement these first:

### 1. `email.ingest`

Reference implementation:

```text
Gmail
```

Responsibilities:

- receive/read message
- expose message metadata
- expose attachment references
- normalize input
- preserve provider identifiers for deduplication

### 2. `document.extract`

Initial implementations:

```text
OpenAI Vision or equivalent API
Python-based fallback where appropriate
```

Responsibilities:

- receive document reference
- extract text/structured content
- return confidence/status where available
- expose partial/failure conditions

### 3. `llm.structured_extract`

Responsibilities:

- accept text + schema
- request structured output
- validate returned structure
- distinguish model output failure from transport/API failure

### 4. `storage.record`

Initial implementation:

```text
PostgreSQL
```

Responsibilities:

- insert/upsert
- idempotency
- duplicate handling
- return canonical record identifier

### 5. `notify.send`

Initial implementations:

```text
Email
Slack or Telegram
```

Responsibilities:

- receive normalized notification payload
- produce explicit delivery result
- avoid duplicate side effects when retried

---

## 15. First portfolio workflow

Build one complete workflow entirely from framework components.

### Portfolio project: Intelligent Document Intake Pipeline

Business scenario:

A business receives documents through email. Staff currently download attachments manually, read documents, extract information, store the result, and notify another team.

### Workflow

```text
Gmail
  ↓
EmailIngest
  ↓
DocumentExtract
  ↓
LLMStructuredExtract
  ↓
Validate
  ↓
PostgresStore
  ↓
Notify
```

### Example input

```text
Email
+
PDF/image attachment
```

### Expected structured output

```json
{
  "document_type": "invoice",
  "sender": "Example Company",
  "invoice_number": "INV-1001",
  "amount": 1200,
  "currency": "USD",
  "status": "EXTRACTED"
}
```

### Portfolio requirements

The workflow must demonstrate:

- real Gmail or representative email ingestion
- attachment handling
- document extraction
- structured LLM output
- schema validation
- PostgreSQL persistence
- duplicate protection
- notification
- logging
- correlation ID
- retry behavior
- dry-run or mock mode
- replayable fixtures
- visible failure states

This workflow is the first **reference application** of the framework.

It should not contain hidden one-off logic that bypasses the framework without documentation.

---

## 16. Failure scenarios for the portfolio workflow

The portfolio is not complete until failure paths are tested.

Minimum cases:

```text
missing attachment
unsupported document
corrupted document
duplicate email
duplicate attachment
OCR/document extraction failure
LLM timeout
LLM invalid structured output
database unavailable
database conflict
notification failure
workflow restart during processing
retry after partial completion
```

Expected behavior must be documented for every case.

Examples:

```text
LLM fails
→ document remains stored
→ state becomes LLM_PENDING or LLM_FAILED
→ retry is possible
```

```text
Notification fails
→ stored business result is not lost
→ notification status becomes FAILED
→ safe retry does not duplicate prior side effects
```

---

## 17. Repository structure

Initial recommended structure:

```text
workflow-automation-framework/

  contracts/
    module_contract/
    result/
    execution_context/

  modules/
    email_ingest/
      definition.yaml
      implementations/
        gmail_n8n/
        gmail_python/

    document_extract/
      definition.yaml
      implementations/
        openai_python/
        openai_n8n/

    llm_structured_extract/
      definition.yaml
      implementations/

    storage_record/
      definition.yaml
      implementations/
        postgres/

    notify_send/
      definition.yaml
      implementations/

  runtimes/
    n8n/
    python/
    http/

  workflows/
    intelligent_document_intake/
      definition.yaml
      instances/
      README.md

  tests/
    fixtures/
    payloads/
    mocks/
    replay_cases/
    failure_cases/

  infra/
    docker/
    postgres/
    n8n/

  docs/
    architecture/
    module_catalog/
    contracts/
    debugging/
```

Do not create empty abstraction layers unless an actual module needs them.

---

## 18. Development stages

### Stage 1 — Freeze the minimal core model

Define only the minimum necessary semantics for:

```text
ModuleContract
ModuleDefinition
ModuleImplementation
Executor
ModuleInstance
WorkflowDefinition
WorkflowInstance
```

Acceptance:

- each concept has one clear responsibility
- no concept duplicates another
- asynchronous and side-effecting modules can be represented
- platform-specific behavior is not forced into the universal contract

Stop condition:

If the universal model forces materially different semantics together, keep the difference explicit instead of hiding it behind abstraction.

---

### Stage 2 — Implement the first three contrasting modules

Implement:

```text
GmailIngest
DocumentExtract
PostgresStore
```

These are deliberately different:

```text
event/input integration
compute/API processing
stateful side-effecting storage
```

Use them to validate whether the core abstraction is actually useful.

Acceptance:

- all three can use the framework without excessive exceptions
- input/output/error contracts remain understandable
- runtime-specific details stay in implementations/executors

---

### Stage 3 — Build the first end-to-end portfolio workflow

Add:

```text
LLMStructuredExtract
Notify
```

Compose:

```text
Gmail
→ DocumentExtract
→ LLMStructuredExtract
→ Validate
→ Postgres
→ Notify
```

Acceptance:

- one complete real workflow runs
- module boundaries are visible
- implementations are replaceable in principle
- workflow behavior can be demonstrated to a client

---

### Stage 4 — Build safe-debug infrastructure

Implement:

```text
fixtures
mocks
dry-run
replay
correlation IDs
structured logs
failure injection
```

Acceptance:

A captured sanitized production-like payload can be replayed without triggering unintended external side effects.

---

### Stage 5 — Reliability hardening

Add and verify where required:

```text
idempotency
duplicate handling
timeouts
retry
partial failure handling
state/checkpoint
concurrency assumptions
rate limits
backpressure
recovery
```

Acceptance:

Operational claims must be backed by tests or clearly bounded assumptions.

---

### Stage 6 — Extend module catalog from market demand

Add modules based on real freelance jobs rather than imagined completeness.

Candidate integrations:

```text
Google Drive
Dropbox
Slack
Telegram
HubSpot
GoHighLevel
Stripe
Calendar
Supabase
Sheets
CRM APIs
document providers
```

Rule:

> Add a module because a real workflow or recurring job pattern needs it, not merely to make the framework look large.

---

### Stage 7 — Job-specific binding

For a real freelance job:

```text
client requirement
        ↓
map to existing capabilities
        ↓
reuse existing implementations where compatible
        ↓
implement only missing adapters / business rules
        ↓
create job-specific module instances
        ↓
compose workflow instance
```

The client workflow may therefore reuse most of the framework without pretending that every environment is identical.

---

## 19. Business-rule boundary

Business rules are not universal framework behavior.

Example client statement:

> High-value leads should be escalated.

Must become an explicit workflow/job rule such as:

```text
budget >= 5000 → HIGH
existing strategic account → HIGH
otherwise → model recommendation
```

Business rules belong in:

```text
workflow definition
job-specific policy
business_rule module configuration
```

They should not be silently hardcoded into generic modules.

---

## 20. Research role

`workflow_module_risk_researcher` may investigate:

- provider/API behavior
- n8n version-specific behavior
- runtime constraints
- queue/concurrency behavior
- provider retry semantics
- database guarantees
- known integration limitations

It returns:

- current sources
- exact supported claims
- applicable versions/conditions
- failure scenarios
- candidate mitigations
- unresolved questions

The researcher does not:

- silently alter architecture
- claim runtime verification
- implement modules without an implementation task
- contact clients

Research informs the framework but does not replace testing.

---

## 21. Verification levels

Always distinguish:

```text
DESIGNED
IMPLEMENTED
TESTED_WITH_FIXTURE
LOCALLY_VERIFIED
STAGING_VERIFIED
PRODUCTION_VERIFIED
CLIENT_ACCEPTED
```

Evidence from logs, screenshots, exported workflow definitions, or sanitized payloads can support diagnosis but must not be mislabeled as live verification.

---

## 22. Production debugging boundary

When working with client infrastructure:

Prefer:

```text
observe
→ capture sanitized evidence
→ reproduce
→ patch
→ regression test
→ controlled deployment
```

Avoid:

```text
edit production blindly
→ repeatedly trigger live side effects
→ infer success from partial logs
```

Minimum debugging information should include where possible:

```text
workflow/run ID
correlation ID
module instance
implementation/runtime
input shape
result status
structured error
attempt
timestamps
external provider response metadata
```

Secrets and personal/sensitive data must be minimized and redacted.

---

## 23. Initial authoritative research anchors

Research must be rechecked against the actual platform/version used in a real implementation.

Starting examples:

- n8n queue mode, worker behavior, concurrency, binary-data and deployment constraints.
- PostgreSQL `INSERT ... ON CONFLICT` and transaction isolation guarantees.
- Provider-specific webhook, OAuth, retry, timeout, and rate-limit documentation.

These sources support implementation decisions but do not create universal guarantees.

---

## 24. Anti-overengineering rules

The framework must not become a project that prevents actual automation work.

Rules:

1. Do not create an abstraction without at least one concrete consumer.
2. Prefer two or three real implementations before generalizing a pattern.
3. Do not make every module support every execution style.
4. Keep provider-specific behavior explicit.
5. A working portfolio workflow has higher priority than an elegant unused abstraction.
6. Reuse should emerge from repeated requirements.
7. Refactor after evidence, not before evidence.
8. The framework exists to make future jobs faster and safer, not to maximize architectural complexity.

---

## 25. Success criteria

The framework is considered useful when:

- a capability can have multiple implementations
- implementations can run under different executors where appropriate
- workflow definitions are not tightly coupled to one provider
- module instances expose configuration clearly
- failure behavior is explicit
- workflows can be replayed and debugged safely
- at least one complete portfolio workflow runs using the framework
- a second workflow can reuse multiple existing modules without rewriting them
- a future job can be decomposed into:
  - existing capabilities
  - reusable implementations
  - missing adapters
  - job-specific business rules

The final objective is not merely:

> "I know n8n."

It is:

> "I have a reusable automation engineering system that lets me translate a business process into a testable, observable, production-oriented workflow and replace individual implementations without rebuilding the entire system."

---

## 26. Immediate next action

Do not expand the framework further before testing it.

Next implementation sequence:

```text
1. Freeze minimal core model
2. Implement GmailIngest
3. Implement DocumentExtract
4. Implement PostgresStore
5. Validate the abstraction against those three modules
6. Add LLMStructuredExtract
7. Add Notify
8. Build Intelligent Document Intake portfolio workflow
9. Add mock / dry-run / replay
10. Run failure scenarios
11. Refactor contract only where real implementations prove it necessary
```

This sequence deliberately develops the framework and its first portfolio instance together.

## Implementation closeout

The user authorized implementation of the full reference portfolio. The code and docs cover all six pipeline modules plus the framework/API. The synthetic workflow completed all six steps through the local HTTP server and the Dockerized API in `DRY_RUN`; authenticated `MOCK` runs exercised sequential idempotent persistence against the Compose PostgreSQL service. The Docker Compose stack starts successfully and n8n health responds, but importing/executing the n8n workflow and live Gmail/OpenAI/Slack integrations remain unverified. See [verification](project-state/verification.md) for exact checks and remaining gaps.
