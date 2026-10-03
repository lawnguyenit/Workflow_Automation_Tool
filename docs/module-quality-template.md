# Reusable module specification template

Copy this file for a module when a real job selects the capability and target. Remove a section only when its reason is recorded as not applicable.

## Identity

- **Module ID / stage:**
- **Purpose and non-goals:**
- **Contract version:**
- **Implementation/runtime and tested version(s):**
- **Status:** proposed / implemented / verified / accepted
- **Owner:**

## Interface

- **Input shape:** one item / many items / batch / stream / binary or reference
- **Input schema and required fields:**
- **Output shape/schema:**
- **Ordering and per-item behavior:**
- **Validation and rejection behavior:**
- **Payload, duration, rate, or volume limits and evidence:**
- **Configuration and prerequisites:**

## Behavior and side effects

- **Reads/writes/sends and affected systems:**
- **Success condition:**
- **Partial result behavior:**
- **Duplicate detection key and scope:**
- **Idempotency guarantee and enforcement point:**
- **Same key with changed input:**
- **Timeout with uncertain outcome:**
- **Concurrent execution safety and shared-state risks:**

## Failure handling and recovery

- **Error codes/categories:**
- **Retryable / non-retryable / unknown cases:**
- **Retry budget, backoff owner, and retry safety:**
- **Rate limiting, overload, or backpressure response:**
- **Checkpoint/replay boundary:**
- **Recovery, reconciliation, or manual repair steps:**
- **Quarantine/dead-letter behavior, if applicable:**

## Operations and security

- **Logs/metrics/status fields:**
- **Correlation IDs and diagnostic references:**
- **Secrets and minimum permissions:**
- **Sensitive data, redaction, retention, and deletion:**
- **Alerts and operator action:**
- **Capacity assumptions and downstream limits:**
- **Minimum sanitized debug package when instance access is unavailable:**
- **Evidence level:** static inspection / fixture reproduction / authorized live test / not tested
- **What cannot be established without client-side access or credentials:**

## Compatibility and evidence

- **Supported platform/version/edition/configuration:**
- **Known incompatible combinations:**
- **Checks run and exact evidence:**
- **Failure cases exercised:**
- **Not tested / known limitations:**
- **Migration, rollback, and change impact:**
- **User acceptance:**

## Git change description

When committing or publishing this module, describe:

1. What capability was added or changed and which pipeline stage it serves.
2. Its input/output and supported runtime versions.
3. Idempotency, duplicate, concurrency, retry, and overload behavior.
4. Failure/recovery and observability behavior.
5. Security/data handling, dependencies, and limits.
6. Exact checks run, what remains unverified, and breaking changes.

Never claim a guarantee beyond the recorded evidence.
