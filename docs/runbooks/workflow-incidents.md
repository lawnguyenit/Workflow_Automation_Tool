# Workflow incident operator notes

These routes are available on the local/private API. Set `$ApiBase` to the trusted API address and `$Headers` using the API key from the protected environment; never put the key in a checked-in script, workflow export, or chat.

```powershell
$ApiBase = 'http://127.0.0.1:8000'
$Headers = @{ Authorization = "Bearer $env:WORKFLOW_API_KEY" }
Invoke-RestMethod "$ApiBase/v1/incidents" -Headers $Headers
Invoke-RestMethod "$ApiBase/v1/incidents/<incident-uuid>" -Headers $Headers
```

Before resolving an incident, inspect the actual source/provider and workflow execution using the run ID held by the operator. The incident API deliberately does not store raw run IDs or error text. Unknown provider outcomes must be reconciled against provider state before any manual replay.

```powershell
$Body = @{ resolution_code = 'PROVIDER_STATE_CONFIRMED' } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "$ApiBase/v1/incidents/<incident-uuid>/resolve" `
  -Headers $Headers -ContentType 'application/json' -Body $Body
```

Accepted resolution codes: `RETRY_COMPLETED`, `PROVIDER_STATE_CONFIRMED`, `MANUAL_REPAIR`, `DUPLICATE_CONFIRMED`, and `DISMISSED`. These values acknowledge a human action; they do not execute it or prove it occurred. `GET /v1/incidents` returns at most 100 items and has no pagination. Incident writes are best effort, so a missing record does not prove a run succeeded.
