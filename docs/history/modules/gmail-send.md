# Gmail send module (v1)

Status: implemented and verified with synthetic HTTP transport plus local PostgreSQL outbox. One separate self-addressed test was sent through the connected Gmail app; that does not exercise this module or n8n.

## Contract

- Capability: `gmail.send`; instance: `portfolio.gmail_send`; implementation: Gmail API v1 `users.messages.send`.
- Input is exactly `{ "idempotency_key", "to", "subject", "text_body" }`. One recipient, plain text only, no attachments or CC/BCC. The authenticated Gmail account supplies the sender; the module cannot impersonate another sender address.
- Requires `GOOGLE_GMAIL_ACCESS_TOKEN` with the narrow `https://www.googleapis.com/auth/gmail.send` scope and a configured PostgreSQL `notification_outbox`.
- `idempotency_key` must be stable for the business event/message purpose (for example, one source event plus `ack`). The module hashes it together with the non-sensitive configured account scope. Same key plus changed recipient/subject/body is rejected by the persisted payload fingerprint.
- Body is limited to 8 KiB UTF-8, subject to 200 characters, one email recipient, one message per call. The outbox also bounds serialized intent to 16 KiB and the Gmail API JSON request to 32 KiB.
- Public output excludes recipient, subject, body, and provider response content. It returns a provider message ID only when a send response is verified.

## Side effects and recovery

## Cost and client account

- Synthetic transport/PostgreSQL tests do not send email and require no API usage.
- Google currently lists standard Gmail API calls as no additional cost within its usage thresholds; quotas apply, and Google says charges above the daily threshold are planned later in 2026. Check [Gmail API quota and pricing](https://developers.google.com/workspace/gmail/api/reference/quota) before a live client run. The sender mailbox or Google Workspace plan is client-owned and may have its own subscription cost.
- A live send requires the client's protected OAuth credential with the `gmail.send` scope and explicit authorization of the recipient/content. The scope is classified by Google as sensitive; a public OAuth app may need verification. No portfolio live send is required for module verification.

- `DRY_RUN` and `REPLAY` return a preview; `MOCK` returns a mock result. These modes do not touch PostgreSQL or Gmail.
- `REAL` writes the message intent to the existing PostgreSQL outbox, conditionally claims `PENDING → SENDING`, then calls Gmail. A concurrent caller cannot claim the same intent. `SENT` duplicates do not send again. Changed content under the same key fails closed.
- Gmail's send API does not expose an idempotency token. A timeout, transport failure, server error, malformed success body, or failure recording the confirmed send becomes `UNKNOWN`/`SENDING`; subsequent calls are held for reconciliation and are never resent automatically. Check Gmail Sent and the outbox record before changing its state.
- Gmail 429 responses return the intent to `PENDING` and expose a bounded `Retry-After` value when supplied. The caller must wait and schedule the retry; the module has no background queue or automatic retry loop. Other 4xx errors are recorded as failed and require correction/reconciliation.
- After confirmed delivery the module clears `notification_outbox.payload` and retains its one-way payload fingerprint, state, key hash, and timestamps. If that redaction fails, the module returns review-required and a later same-key call attempts redaction without resending.
- Unknown outcomes retain recipient/message content in the outbox until an operator reconciles and clears or resolves the record. Protect PostgreSQL access and define an operational retention window before using real prospect data.

## Evidence and limits

- Unit tests exercise MIME encoding, input boundaries, preview/mock no-side-effects, sent dedupe, changed-payload conflict, sequential claim behavior, 429 handling (delta-seconds and HTTP-date), auth rejection, outbox transition failure, malformed success, uncertain timeout hold, and output redaction. A separate PostgreSQL integration check verifies same-key concurrent claims.
- Docker/PostgreSQL integration uses synthetic recipients and a mocked Gmail HTTP response. It is not a live Gmail API call.
- A separate authorized self-test email was sent using the connected Codex Gmail integration to the authenticated user's own mailbox and found in its `SENT` messages. This confirms that account path, not the local API's OAuth token, n8n credential, or this module.
- The token is injected at runtime and this implementation does not refresh OAuth tokens. Production use needs a secure OAuth credential broker or a native n8n Gmail credential, least-scope consent, sender policy, retention, and controlled rate-limit scheduling.
- This adapter sends a single supplied message; it does not decide which leads qualify, generate business-approved copy, honor reply/booking/opt-out/manual-pause states, or guarantee end-to-end workflow success.

## Operator recovery for known rejection

For a `FAILED` intent only, first confirm Gmail rejected the request without accepting the message and correct the credential or mapping. Then an authorized operator may requeue that exact hashed key:

```sql
UPDATE notification_outbox
SET state = 'PENDING', error_code = NULL, updated_at = now()
WHERE idempotency_key = '<64-character hashed key from the module result>'
  AND destination = 'gmail:<configured account scope>'
  AND state = 'FAILED';
```

Do not use this for `SENDING`, `UNKNOWN`, or `SENT`. For those states, inspect the Gmail Sent mailbox first and reconcile the outbox before any operator change. Keep real recipient/body content out of SQL client history and incident notes.

## Official reference

- [Gmail API `users.messages.send`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)
- [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
