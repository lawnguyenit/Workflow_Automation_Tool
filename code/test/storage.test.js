'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { normalizeForStorage, NormalizationError } = require('../src/normalize');
const { storeOutcome } = require('../src/store-outcome');
const { capDecision, parseLimit } = require('../src/ai-cap');

const base = { document_type: 'invoice', sender: ' Example Co ', invoice_number: ' INV-1 ', amount: 1200.5, currency: 'usd', status: 'EXTRACTED', confidence: 0.9, extraction_provider: 'gemini', extraction_model: 'm', record_key: 'invoice:x', source_ids: ['a'] };

test('normalizeForStorage keeps only stored fields and normalizes', () => {
  const { invoice_data: d, payload_fingerprint: fp } = normalizeForStorage(base);
  assert.deepEqual(Object.keys(d).sort(), ['amount', 'confidence', 'currency', 'document_type', 'extraction_model', 'extraction_provider', 'invoice_number', 'sender', 'status']);
  assert.equal(d.amount, '1200.5');
  assert.equal(d.currency, 'USD');
  assert.equal(d.invoice_number, 'INV-1');
  assert.equal(d.sender, 'Example Co');
  assert.match(fp, /^[a-f0-9]{64}$/);
});

test('fingerprint is stable across key order and changes with payload', () => {
  const a = normalizeForStorage(base).payload_fingerprint;
  const reordered = Object.fromEntries(Object.entries(base).reverse());
  assert.equal(normalizeForStorage(reordered).payload_fingerprint, a);
  assert.equal(normalizeForStorage({ ...base, record_key: 'other' }).payload_fingerprint, a, 'non-stored fields do not count');
  assert.notEqual(normalizeForStorage({ ...base, amount: 1200.51 }).payload_fingerprint, a);
});

test('defaults and rejections', () => {
  const { invoice_data: d } = normalizeForStorage({ invoice_number: 'X', amount: '10.50', currency: 'EUR' });
  assert.equal(d.document_type, 'invoice');
  assert.equal(d.status, 'EXTRACTED');
  assert.equal(d.amount, '10.5');
  const code = (r) => { try { normalizeForStorage(r); } catch (e) { assert.ok(e instanceof NormalizationError); return e.code; } return null; };
  assert.equal(code({ ...base, amount: 'abc' }), 'INVALID_AMOUNT');
  assert.equal(code({ ...base, amount: Infinity }), 'INVALID_AMOUNT');
  assert.equal(code({ ...base, currency: 'US' }), 'INVALID_CURRENCY');
  assert.equal(code({ ...base, invoice_number: '' }), 'MISSING_INVOICE_NUMBER');
  assert.equal(code({ ...base, confidence: 2 }), 'INVALID_CONFIDENCE');
  assert.equal(code({ ...base, confidence: true }), 'INVALID_CONFIDENCE');
  assert.equal(code(null), 'INVALID_RECORD');
});

test('storeOutcome: inserted, duplicate, conflict, unknown', () => {
  assert.deepEqual(storeOutcome([{ outcome: 'inserted', id: 1, payload_fingerprint: 'f' }], 'f'), { outcome: 'STORED', invoice_id: '1', reason: null });
  assert.deepEqual(storeOutcome([{ outcome: 'existing', id: 1, payload_fingerprint: 'f' }], 'f'), { outcome: 'DUPLICATE', invoice_id: '1', reason: null });
  assert.deepEqual(storeOutcome([{ outcome: 'existing', id: 1, payload_fingerprint: 'f' }], 'g'), { outcome: 'CONFLICT', invoice_id: '1', reason: 'IDEMPOTENCY_KEY_PAYLOAD_CHANGED' });
  assert.equal(storeOutcome([], 'f').outcome, 'UNKNOWN');
  assert.equal(storeOutcome([{}], 'f').outcome, 'UNKNOWN');
});

test('capDecision', () => {
  assert.deepEqual(capDecision([{ calls: 3 }], 50), { allowed: true, calls_today: 3, daily_limit: 50, reason: null });
  assert.equal(capDecision([], 50).reason, 'DAILY_AI_CAP_REACHED');
  assert.equal(capDecision([{}], 50).allowed, false);
  assert.equal(capDecision([{ calls: 51 }], 50).allowed, false);
  assert.equal(capDecision([{ calls: 'x' }], 50).reason, 'AI_LEDGER_UNREADABLE');
  assert.equal(capDecision([{ calls: 1 }], '0').allowed, false);
  assert.equal(parseLimit('20'), 20);
  assert.throws(() => parseLimit(-1));
  assert.throws(() => parseLimit(1.5));
});
