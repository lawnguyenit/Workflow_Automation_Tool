# Runbook: incidents

Every failure in any workflow runs `error handler`, which writes one row to `incident_log` and sends
one alert email. The row holds ids, the failed node, the error class and a masked 300-character
message; no email body, sender or invoice data (see [architecture](../architecture.md#error-path)).
This runbook was written from the schema in `infra/postgres/10-invoice-intake.sql` and has not been
exercised on a live deployment.

Open incidents:

```bash
docker compose exec postgres psql -U workflow -d workflow_automation -c \
  "SELECT id, occurred_at, workflow_name, failed_node, error_name, error_message
     FROM incident_log WHERE acknowledged_at IS NULL ORDER BY occurred_at DESC;"
```

Steps:

1. Open the execution in n8n (Executions tab, `execution_id` from the row). Failed executions keep item data.
2. Decide per cause. A failure after a successful store (for example Gmail send) cannot be fixed by
   re-running the execution: the invoice is then found as a duplicate and skipped (limit L1). Notify by hand.
3. A store error about not being able to read the existing row comes from two executions inserting one key at
   the same moment; a re-run is safe.
4. When handled: `UPDATE incident_log SET acknowledged_at = now() WHERE id = <id>;`
