'use strict';
// Interpret the single-statement insert in sub/store-invoice. The query returns
// one row with outcome 'inserted' | 'existing' plus the stored fingerprint.
// Same key + same fingerprint = DUPLICATE (nothing written).
// Same key + different fingerprint = CONFLICT (first write kept, nothing overwritten).

function storeOutcome(rows, payloadFingerprint) {
  const row = Array.isArray(rows) ? rows[0] : undefined;
  if (!row || row.id === undefined || row.id === null) {
    // A concurrent insert can commit after this statement's snapshot; the row is then
    // invisible to the same statement. Re-running the sub-workflow resolves it.
    return { outcome: 'UNKNOWN', invoice_id: null, reason: 'STORE_OUTCOME_UNKNOWN_RETRY_SAFE' };
  }
  if (row.outcome === 'inserted') return { outcome: 'STORED', invoice_id: String(row.id), reason: null };
  if (row.payload_fingerprint === payloadFingerprint) return { outcome: 'DUPLICATE', invoice_id: String(row.id), reason: null };
  return { outcome: 'CONFLICT', invoice_id: String(row.id), reason: 'IDEMPOTENCY_KEY_PAYLOAD_CHANGED' };
}

module.exports = { storeOutcome };
