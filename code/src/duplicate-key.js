'use strict';
// Duplicate / idempotency key for one invoice attachment:
//   sha256("<gmail message id>\n<sha256 of the attachment bytes>")
// The same PDF re-sent in a new email gets a new key on purpose (different message);
// the same email re-delivered or re-polled gets the same key.

const { sha256Hex } = require('./sha256');

const HEX64 = /^[a-f0-9]{64}$/;

function duplicateKey(messageId, attachmentSha256) {
  if (typeof messageId !== 'string' || !messageId.trim()) throw new Error('message_id must be a non-empty string');
  if (typeof attachmentSha256 !== 'string' || !HEX64.test(attachmentSha256)) {
    throw new Error('attachment_sha256 must be 64 lowercase hex characters');
  }
  return sha256Hex(`${messageId.trim()}\n${attachmentSha256}`);
}

/** Interpret the row list returned by the "find invoice by key" query. */
function duplicateCheckResult(key, rows) {
  const row = Array.isArray(rows) ? rows.find((r) => r && r.id !== undefined && r.id !== null) : undefined;
  return {
    idempotency_key: key,
    is_duplicate: Boolean(row),
    existing_invoice_id: row ? String(row.id) : null,
  };
}

module.exports = { duplicateKey, duplicateCheckResult, HEX64 };
