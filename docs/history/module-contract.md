# Shared module contract — draft 0.1

**Status:** Draft for review. This is a documentation-level proposal, not a frozen schema or executable runtime contract.

## 1. Purpose and boundary

A module is one bounded capability in a workflow, such as receiving a webhook, parsing a PDF, validating records, writing a database row, or sending a notification. A module declares its input/output shape and operational behavior so another module or workflow can call it without guessing.

The contract is platform-neutral. An n8n implementation may map a module to a sub-workflow or a documented node group. A code implementation may map it to a function, service, or package API. These implementations can expose different capabilities; do not claim parity unless verified.

## 2. Separate module definition from execution result

### Module definition (catalog metadata)

Each module specification should declare:

| Field | Meaning |
|---|---|
| `module_id` | Stable, namespaced identifier. |
| `contract_version` | Version of the public input/output and behavior contract. |
| `implementation` | Runtime, product/node versions, and implementation version when one exists. |
| `purpose` | One capability and its boundary. |
| `input_schema` / `output_schema` | Types, required/optional fields, units, formats, and validation behavior. |
| `execution_shape` | One item, many items, batch, stream, or binary/reference payload; specify supported modes rather than assuming all. |
| `side_effects` | None, read-only, or writes/sends; name the affected system and whether the outcome can be ambiguous after timeout. |
| `capabilities` | Supported idempotency, cancellation, partial output, checkpoint/replay, and concurrency modes. Mark unsupported or unknown explicitly. |
| `limits` | Measured/known payload, rate, duration, volume, and parallelism bounds with their source and conditions. |
| `configuration` | Required values and secure credential references; never embed secret material. |
| `compatibility` | Tested runtime/platform/version/edition combinations and known incompatibilities. |

### Execution context

Pass only fields needed by the selected implementation. Common candidates include `run_id`, `correlation_id`, `attempt`, `started_at`, and an optional caller-provided `idempotency_key`. Define who creates each value, its uniqueness scope, and how it propagates. A trace identifier is not automatically an idempotency key. A run ID alone does not guarantee deduplication.

### Execution result

An implementation should document an output equivalent to:

```json
{
  "contract_version": "0.1",
  "module_id": "stage.capability",
  "status": "succeeded | partial | failed | needs_review",
  "outputs": [],
  "output_refs": [],
  "errors": [],
  "warnings": [],
  "metrics": {},
  "context": {
    "run_id": "...",
    "correlation_id": "...",
    "attempt": 1
  },
  "started_at": "...",
  "finished_at": "..."
}
```

This JSON is illustrative. Implementations may map it to a platform-native shape if the mapping is documented and preserves the declared semantics. Large files or datasets should use a bounded payload or access-controlled reference; document reference lifetime, permissions, and cleanup.

Allowed statuses must have precise module-specific meanings:

- `succeeded`: all declared work completed according to the acceptance condition.
- `partial`: some work completed and some did not; identify affected items and whether side effects occurred.
- `failed`: the declared success condition was not reached; side effects may still have occurred and must be stated.
- `needs_review`: automatic processing stopped because a person or external decision is needed.

## 3. Error record

Each error should have a stable `code`, `category`, safe human-readable message, retry classification (`retryable`, `not_retryable`, `unknown`), and diagnostic reference/correlation context where available. Include a suggested retry delay only when the source/platform supplies a meaningful value. Keep secrets and unnecessary sensitive payloads out of error text.

`unknown` is not permission to retry automatically. If the system timed out after sending a write or notification, the remote side effect may have happened. The module must report that uncertainty and offer a safe reconciliation or idempotency path when one exists.

## 4. Required operational declarations

Every module declares, or explicitly marks unknown/not applicable:

1. **Duplicate and idempotency behavior:** deduplication key, key scope/lifetime, where uniqueness is enforced, and behavior for the same key with changed input.
2. **Concurrent execution:** whether parallel calls are safe, serialized, keyed, or unsupported; identify shared state and database/downstream constraints.
3. **Retry and timeout:** which error classes can retry, maximum budget and backoff owner, whether retry repeats side effects, and what happens when outcome is unknown.
4. **Partial failure:** whether successes are kept, rolled back, quarantined, or returned with per-item status.
5. **Capacity and backpressure:** the measured operating range, limiting dependency, response to queue growth/rate limiting/resource exhaustion, and the point where work is rejected, delayed, or paused.
6. **Replay and recovery:** required retained inputs/checkpoints, replay boundary, duplicate protection, and recovery steps after process or service failure.
7. **Observability:** useful counters/status/timing, identifiers to correlate, safe diagnostic details, alert conditions, and retention controls.
8. **Security and data handling:** minimum permissions, secret references, sensitive fields, redaction, retention/deletion, and any external data transfer.
9. **Debug access requirements:** the minimum sanitized workflow/configuration, input/output sample, error context, platform version, and timing information needed to diagnose the module when direct access to the client's n8n instance or downstream service is unavailable.

Do not use generic statements such as “exactly once”, “no duplicates”, “unlimited scale”, or “safe to retry” without a bounded, evidence-backed definition.

## 5. Composition rules

- State whether a module emits one result, one result per item, or a batch result. A caller must not infer this from sample data.
- State whether order is preserved and whether outputs can be emitted before all inputs finish.
- Make schema changes explicit and versioned. Document breaking changes and migration requirements.
- Keep business decisions (for example, identity key, conflict winner, acceptable extraction confidence, or notification policy) configurable and visible; do not silently guess them in generic modules.
- Keep cross-cutting policies attachable to the stages that need them; infrastructure should not be duplicated as if it were a business-data stage.
- Workflow dependencies express execution order. A step may additionally require named dependency steps to have `SUCCESS`; if any requirement is missing or non-success, that step is skipped and the result reports its unmet gate. Use this for side effects that must not run after review/partial results.
- Workflow status preserves the highest-severity executed result (`FAILURE` > `REVIEW_REQUIRED` > `PARTIAL` > `SUCCESS`), even when a later persistence/cleanup step succeeds. `result` still contains the business output from the last executed step.

## 6. Verification expectations for future implementations

For the target platform and version, verify normal inputs, invalid inputs, duplicate/replay cases, overlapping/concurrent writes when relevant, transient and permanent failures, timeouts with uncertain side effects, partial batches, load limits, and recovery. Label fixture, integration, and live target evidence separately. Unsupported scenarios remain limitations, not implied guarantees.

For client debugging with limited access, distinguish among static workflow inspection, reproduction with a sanitized fixture, authorized execution in the client's environment, and checks that could not be performed. Ask for only the minimum sanitized evidence needed: exported workflow JSON with secrets removed, n8n version/edition/hosting mode, failing node and exact error, relevant input/output at that boundary, execution mode and timestamp/time zone, and whether the side effect may already have occurred. Do not present fixture reproduction as proof about the live system.

## 7. Paper-fit check against contrasting module shapes

This check tests whether the draft can describe different module behaviors without forcing one execution model onto them. It is design reasoning only; none of these modules is implemented or runtime-verified.

| Example shape | Contract areas that apply | Behavior that must remain module-specific |
|---|---|---|
| Gmail input | Trigger/poll execution shape, message identity, batch output, attachment references, checkpoint/replay, permission and secret references | Mailbox scope, provider cursor semantics, attachment handling, and whether reading/acknowledgment changes remote state |
| PDF parsing | One document or binary reference in; one or many extracted records out; schema/validation errors; partial result and provenance | OCR/parser confidence, page limits, malformed-page handling, and whether a parse can return usable partial fields |
| PostgreSQL storage | Batch/item output shape, declared unique key, transaction and upsert behavior, concurrent writes, per-row errors | Conflict policy, transaction boundary, isolation requirements, and what constitutes same entity are job/business choices |
| Slack output | Write side effect, request/response mapping, timeout ambiguity, duplicate/retry behavior, rate limits, safe diagnostics | Provider's idempotency/reconciliation support, destination/channel rules, and whether a timeout can be confirmed by lookup |

**Fit result:** the draft can represent event/polling, binary-reference, batch, database-write, and notification shapes if each implementation declares its own capabilities and side effects. This does not prove the illustrative result envelope is mandatory or that any platform provides the listed guarantees; those remain open until concrete modules and targets are selected.

## 8. Open items before freezing

- Whether the shared result envelope is required across runtimes or only used as documentation/mapping guidance.
- The minimum schema language and compatibility policy.
- Whether binary content is passed inline or by reference for the first concrete platform.
- The module catalog and change-description format after the first real module is selected.
