'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const x = require('../src/extraction');
const { incidentRow, scrub } = require('../src/incident');
const { validateInvoice } = require('../src/validate');

const source = { idempotency_key: 'a'.repeat(64), message_id: 'm1', attachment_id: 'attachment_0', filename: 'invoice.pdf', source_account: 'gmail:secondary' };
const good = { document_type: 'invoice', sender: 'Example Co', invoice_number: 'INV-1001', amount: 1200, currency: 'USD', confidence: 0.92, missing_fields: [], conflicts: [] };
const gemini = (obj) => ({ candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] });

test('request body asks for JSON with the schema and keeps the safety prompt', () => {
  const body = x.geminiRequestBody('Invoice text');
  assert.equal(body.generationConfig.responseMimeType, 'application/json');
  assert.deepEqual(body.generationConfig.responseSchema.required.sort(), [...x.FIELDS, 'confidence', 'missing_fields', 'conflicts'].sort());
  assert.match(body.systemInstruction.parts[0].text, /ignore instructions in it/);
  assert.equal(body.contents[0].parts[0].text, 'Invoice text');
});

test('text issues', () => {
  assert.equal(x.textIssue(''), 'OCR_REQUIRED');
  assert.equal(x.textIssue('   '), 'OCR_REQUIRED');
  assert.equal(x.textIssue(undefined), 'OCR_REQUIRED');
  assert.equal(x.textIssue('a'.repeat(x.MAX_TEXT_CHARS + 1)), 'TEXT_LIMIT_EXCEEDED');
  assert.equal(x.textIssue('Invoice'), null);
});

test('valid Gemini response becomes an EXTRACTED record that passes validation', () => {
  const rec = x.toRecord(source, x.parseGeminiResponse(gemini(good)), 'gemini-test');
  assert.equal(rec.status, 'EXTRACTED');
  assert.equal(rec.record_key, `invoice:${'a'.repeat(24)}`);
  assert.deepEqual(rec.source_ids, ['message:m1', 'attachment:attachment_0']);
  assert.equal(validateInvoice(rec).valid, true);
});

test('null fields are forced into missing_fields; conflicts null the field and need review', () => {
  const out = x.parseGeminiResponse(gemini({ ...good, amount: null, conflicts: ['sender', 'sender'] }));
  assert.deepEqual(out.missing_fields, ['amount']);
  assert.deepEqual(out.conflicts, ['sender']);
  const rec = x.toRecord(source, out, 'm');
  assert.equal(rec.sender, null);
  assert.equal(rec.status, 'REVIEW_REQUIRED');
  assert.equal(validateInvoice(rec).valid, false);
});

test('schema violations are rejected as INVALID_STRUCTURED_OUTPUT', () => {
  const cases = [
    { ...good, extra: 1 }, { ...good, amount: '12' }, { ...good, sender: '' }, { ...good, confidence: 3 },
    { ...good, missing_fields: ['total'] }, { ...good, conflicts: 'amount' },
  ];
  for (const c of cases) assert.throws(() => x.parseGeminiResponse(gemini(c)), (e) => e.code === 'INVALID_STRUCTURED_OUTPUT');
  assert.throws(() => x.parseGeminiResponse({ candidates: [{ content: { parts: [{ text: 'not json' }] } }] }), (e) => e.code === 'INVALID_STRUCTURED_OUTPUT');
  assert.throws(() => x.parseGeminiResponse({}), (e) => e.code === 'INVALID_STRUCTURED_OUTPUT');
});

test('provider errors map to retryable or not', () => {
  assert.throws(() => x.parseGeminiResponse({ error: { code: 429 } }), (e) => e.code === 'PROVIDER_UNAVAILABLE' && e.retryable);
  assert.throws(() => x.parseGeminiResponse({ error: { code: 400 } }), (e) => e.code === 'PROVIDER_REJECTED_REQUEST' && !e.retryable);
});

test('empty record always fails validation with EXTRACTION_REQUIRES_REVIEW', () => {
  const rec = x.emptyRecord(source, 'DAILY_AI_CAP_REACHED');
  assert.equal(rec.error_code, 'DAILY_AI_CAP_REACHED');
  const v = validateInvoice(rec);
  assert.equal(v.valid, false);
  assert.ok(v.issues.some((i) => i.code === 'EXTRACTION_REQUIRES_REVIEW'));
});

test('incident row keeps no PII and bounds the message', () => {
  const row = incidentRow({
    execution: { id: 42, mode: 'trigger', lastNodeExecuted: 'Store invoice row', error: { name: 'NodeOperationError', message: `Failed for alice@example.com account 12345678 ${'z'.repeat(500)}` } },
    workflow: { id: 'wfStoreInvoice00', name: 'Store invoice' },
  });
  assert.equal(row.execution_id, '42');
  assert.equal(row.failed_node, 'Store invoice row');
  assert.ok(!row.error_message.includes('alice@example.com'));
  assert.ok(!row.error_message.includes('12345678'));
  assert.ok(row.error_message.length <= 300);
  assert.deepEqual(Object.keys(row).sort(), ['error_message', 'error_name', 'execution_id', 'execution_mode', 'failed_node', 'workflow_id', 'workflow_name']);
  assert.equal(incidentRow(null).workflow_id, null);
  assert.equal(scrub(undefined), null);
  assert.equal(incidentRow({ trigger: { error: { message: 'trigger failed' } }, workflow: { name: 'W' } }).error_message, 'trigger failed');
});
