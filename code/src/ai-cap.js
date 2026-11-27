'use strict';
// Daily AI-call cap. The Postgres query in sub/extract-invoice reserves one call
// atomically (INSERT .. ON CONFLICT DO UPDATE .. WHERE calls < limit RETURNING calls);
// this helper turns its result into a decision. No row back = cap reached.

function parseLimit(value) {
  const n = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
  if (!Number.isInteger(n) || n < 0) throw new Error('daily AI call limit must be a non-negative integer');
  return n;
}

function capDecision(rows, dailyLimit) {
  const limit = parseLimit(dailyLimit);
  const row = Array.isArray(rows) ? rows.find((r) => r && r.calls !== undefined && r.calls !== null) : undefined;
  if (!row) return { allowed: false, calls_today: null, daily_limit: limit, reason: 'DAILY_AI_CAP_REACHED' };
  const calls = Number(row.calls);
  if (!Number.isInteger(calls) || calls < 1) return { allowed: false, calls_today: null, daily_limit: limit, reason: 'AI_LEDGER_UNREADABLE' };
  if (calls > limit) return { allowed: false, calls_today: calls, daily_limit: limit, reason: 'DAILY_AI_CAP_REACHED' };
  return { allowed: true, calls_today: calls, daily_limit: limit, reason: null };
}

module.exports = { parseLimit, capDecision };
