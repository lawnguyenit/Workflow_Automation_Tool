'use strict';
// Mirrors legacy/python-modules/tests/unit/processing_llm/test_validate.py case by case.
const test = require('node:test');
const assert = require('node:assert/strict');
const { validateRecords, validateInvoice, buildConfig } = require('../src/validate');

function record(overrides = {}) {
  return {
    record_key: 'invoice:abc123', document_type: 'invoice', sender: 'Example Company',
    invoice_number: 'INV-1001', amount: 1200.0, currency: 'USD', source_ids: ['email-1/attachment-1'],
    extraction_provider: 'local_fixture', extraction_model: 'deterministic-v1', status: 'EXTRACTED',
    confidence: null, missing_fields: [], conflicts: [], ...overrides,
  };
}
const codesOf = (rejected) => new Set(rejected.errors.map((e) => e.code));

test('valid record is accepted and currency normalized', () => {
  const provenance = { source_system: 'gmail', source_account: 'accounts/payables', source_message_id: 'message-1', source_attachment_id: 'attachment-1' };
  const result = validateRecords([record({ currency: 'usd', ...provenance })]);
  assert.equal(result.status, 'SUCCESS');
  assert.equal(result.records[0].currency, 'USD');
  for (const [k, v] of Object.entries(provenance)) assert.equal(result.records[0][k], v);
  assert.deepEqual(result.rejected, []);
});

test('missing required fields are rejected with stable codes', () => {
  const result = validateRecords([record({ sender: null, invoice_number: null, amount: null, currency: null })]);
  assert.equal(result.status, 'REVIEW_REQUIRED');
  const codes = codesOf(result.rejected[0]);
  for (const c of ['MISSING_SENDER', 'MISSING_INVOICE_NUMBER', 'MISSING_AMOUNT', 'MISSING_CURRENCY']) assert.ok(codes.has(c), c);
});

test('invalid numeric amounts are rejected', () => {
  const bad = ['not money', true, NaN, Infinity];
  const result = validateRecords(bad.map((amount, i) => record({ record_key: `inv:${i}`, amount })));
  assert.equal(result.rejected.length, bad.length);
  assert.ok(result.rejected.every((r) => codesOf(r).has('INVALID_AMOUNT')));
});

test('amount thresholds and negative amount have explicit codes', () => {
  const result = validateRecords([record({ record_key: 'negative', amount: -1 }), record({ record_key: 'too-large', amount: 501 })], { minimum_amount: 0, maximum_amount: 500 });
  assert.ok(codesOf(result.rejected[0]).has('AMOUNT_BELOW_MINIMUM'));
  assert.ok(codesOf(result.rejected[1]).has('AMOUNT_ABOVE_MAXIMUM'));
});

test('currency must be three letters and optional allowlist is enforced', () => {
  const result = validateRecords([record({ record_key: 'bad-format', currency: 'US' }), record({ record_key: 'not-allowed', currency: 'JPY' })], { allowed_currencies: ['USD', 'EUR'] });
  assert.ok(codesOf(result.rejected[0]).has('INVALID_CURRENCY'));
  assert.ok(codesOf(result.rejected[1]).has('UNSUPPORTED_CURRENCY'));
});

test('extraction review, conflict and duplicate keys are rejected', () => {
  const result = validateRecords([record({ record_key: 'same' }), record({ record_key: 'review', status: 'REVIEW_REQUIRED', conflicts: ['amount'] }), record({ record_key: 'same' })]);
  assert.equal(result.status, 'PARTIAL');
  assert.ok(codesOf(result.rejected[0]).has('EXTRACTION_REQUIRES_REVIEW'));
  assert.ok(codesOf(result.rejected[1]).has('DUPLICATE_RECORD_KEY'));
});

test('confidence threshold is opt-in', () => {
  assert.equal(validateRecords([record({ confidence: 0.4 })]).status, 'SUCCESS');
  const failing = validateRecords([record({ confidence: 0.4 })], { minimum_confidence: 0.8 });
  assert.ok(codesOf(failing.rejected[0]).has('CONFIDENCE_BELOW_MINIMUM'));
  const missing = validateRecords([record({ confidence: null })], { minimum_confidence: 0.8 });
  assert.ok(codesOf(missing.rejected[0]).has('MISSING_CONFIDENCE'));
});

test('unsupported fields, invalid confidence, invalid source_ids, missing key, non-object', () => {
  const result = validateRecords([
    record({ record_key: 'a', extra: 1 }),
    record({ record_key: 'b', confidence: 1.5 }),
    record({ record_key: 'c', source_ids: [1] }),
    record({ record_key: '  ' }),
    'not an object',
  ]);
  assert.equal(result.status, 'REVIEW_REQUIRED');
  assert.ok(codesOf(result.rejected[0]).has('UNSUPPORTED_FIELD'));
  assert.ok(codesOf(result.rejected[1]).has('INVALID_CONFIDENCE'));
  assert.ok(codesOf(result.rejected[2]).has('INVALID_SOURCE_IDS'));
  assert.ok(codesOf(result.rejected[3]).has('MISSING_RECORD_KEY'));
  assert.ok(codesOf(result.rejected[4]).has('INVALID_RECORD'));
});

test('non-list input fails, config errors are raised', () => {
  assert.equal(validateRecords('x').status, 'FAILURE');
  assert.throws(() => buildConfig({ required_fields: ['nope'] }), /Unsupported required fields/);
  assert.throws(() => buildConfig({ minimum_amount: 10, maximum_amount: 1 }), /cannot exceed/);
  assert.throws(() => buildConfig({ minimum_confidence: 2 }), /between 0 and 1/);
  assert.throws(() => buildConfig({ allowed_fields: ['sender'] }), /record_key/);
});

test('issues are de-duplicated', () => {
  const result = validateRecords([record({ sender: '' })]);
  const sender = result.rejected[0].errors.filter((e) => e.field === 'sender');
  assert.deepEqual(sender.map((e) => e.code), ['MISSING_SENDER', 'INVALID_SENDER']);
});

test('validateInvoice gives the single-invoice contract', () => {
  const ok = validateInvoice(record());
  assert.equal(ok.valid, true);
  assert.equal(ok.status, 'VALID');
  assert.deepEqual(ok.issues, []);
  const bad = validateInvoice(record({ status: 'REVIEW_REQUIRED', error_code: 'OCR_REQUIRED' }));
  assert.equal(bad.valid, false);
  assert.equal(bad.status, 'REVIEW_REQUIRED');
  assert.equal(bad.invoice, null);
  assert.ok(bad.issues.some((i) => i.code === 'EXTRACTION_REQUIRES_REVIEW'));
});
