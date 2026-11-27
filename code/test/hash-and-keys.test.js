'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { sha256Hex } = require('../src/sha256');
const { canonicalJson } = require('../src/canonical-json');
const { duplicateKey, duplicateCheckResult } = require('../src/duplicate-key');

const FIXTURES = path.resolve(__dirname, '..', '..', 'tests', 'fixtures');
const nodeHash = (data) => crypto.createHash('sha256').update(data).digest('hex');

test('sha256 matches node:crypto for strings, unicode and edge lengths', () => {
  for (const s of ['', 'abc', 'hóa đơn 💡', 'x'.repeat(55), 'x'.repeat(56), 'x'.repeat(64), 'y'.repeat(1000)]) {
    assert.equal(sha256Hex(s), nodeHash(Buffer.from(s, 'utf8')), `len ${s.length}`);
  }
});

test('sha256 matches node:crypto for every fixture PDF', () => {
  const dir = path.join(FIXTURES, 'pdf');
  const files = fs.readdirSync(dir).filter((f) => f.endsWith('.pdf'));
  assert.ok(files.length >= 3);
  for (const f of files) {
    const bytes = fs.readFileSync(path.join(dir, f));
    assert.equal(sha256Hex(bytes), nodeHash(bytes), f);
  }
});

test('canonical json sorts keys and keeps unicode', () => {
  assert.equal(canonicalJson({ b: 1, a: [true, null, 'đ'], c: { z: 1, y: 2 } }), '{"a":[true,null,"đ"],"b":1,"c":{"y":2,"z":1}}');
  assert.throws(() => canonicalJson({ a: NaN }));
});

test('duplicate key: same email + same PDF = same key; fixture duplicate pair collides', () => {
  const emails = JSON.parse(fs.readFileSync(path.join(FIXTURES, 'email', 'emails.json'), 'utf8')).fixtures;
  const keys = emails.synthetic_duplicate.map((m) => {
    const bytes = Buffer.from(m.attachments[0].content_base64, 'base64');
    return duplicateKey(m.message_id, sha256Hex(bytes));
  });
  assert.equal(keys[0], keys[1]);
  const single = emails.synthetic_invoice[0];
  const other = duplicateKey(single.message_id, sha256Hex(Buffer.from(single.attachments[0].content_base64, 'base64')));
  assert.notEqual(other, keys[0]);
  assert.equal(keys[0], nodeHash(`fixture-message-dup\n${sha256Hex(Buffer.from(emails.synthetic_duplicate[0].attachments[0].content_base64, 'base64'))}`));
});

test('duplicate key rejects bad input', () => {
  assert.throws(() => duplicateKey('', 'a'.repeat(64)));
  assert.throws(() => duplicateKey('m', 'A'.repeat(64)));
  assert.throws(() => duplicateKey('m', 'abc'));
});

test('duplicateCheckResult reads query rows', () => {
  assert.deepEqual(duplicateCheckResult('k', []), { idempotency_key: 'k', is_duplicate: false, existing_invoice_id: null });
  assert.deepEqual(duplicateCheckResult('k', [{}]), { idempotency_key: 'k', is_duplicate: false, existing_invoice_id: null });
  assert.deepEqual(duplicateCheckResult('k', [{ id: 7 }]), { idempotency_key: 'k', is_duplicate: true, existing_invoice_id: '7' });
});
