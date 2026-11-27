'use strict';
// Field normalization before storage, ported from PostgresStore._validate_payload in
// legacy/python-modules/src/workflow_automation/modules/storage/postgres_store.py.
// The normalized object is what gets stored and fingerprinted.

const { canonicalJson } = require('./canonical-json');
const { sha256Hex } = require('./sha256');

const STORED_FIELDS = [
  'document_type', 'sender', 'invoice_number', 'amount', 'currency', 'status',
  'confidence', 'extraction_provider', 'extraction_model',
];

class NormalizationError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

/** Decimal text for an amount: "1234.50" -> "1234.5", 100 -> "100". Rejects non-finite values. */
function amountText(raw) {
  if (typeof raw === 'number') {
    if (!Number.isFinite(raw)) return null;
    return String(raw);
  }
  if (typeof raw === 'string' && /^-?\d+(\.\d+)?$/.test(raw.trim())) {
    return String(Number(raw.trim()));
  }
  return null;
}

/**
 * Returns { invoice_data, payload_fingerprint } or throws NormalizationError.
 * invoice_data contains only STORED_FIELDS present in the record, amount as decimal text,
 * currency upper-cased, document_type defaulted to "invoice", status to "EXTRACTED".
 */
function normalizeForStorage(record) {
  if (record === null || typeof record !== 'object' || Array.isArray(record)) {
    throw new NormalizationError('INVALID_RECORD', 'record must be an object');
  }
  const amount = amountText(record.amount);
  if (amount === null) throw new NormalizationError('INVALID_AMOUNT', 'amount must be a finite decimal number');
  const currency = typeof record.currency === 'string' ? record.currency.trim().toUpperCase() : null;
  if (!currency || !/^[A-Z]{3}$/.test(currency)) {
    throw new NormalizationError('INVALID_CURRENCY', 'currency must be a three-letter currency code');
  }
  const normalized = {};
  for (const field of STORED_FIELDS) {
    if (Object.prototype.hasOwnProperty.call(record, field) && record[field] !== undefined) normalized[field] = record[field];
  }
  normalized.amount = amount;
  normalized.currency = currency;
  if (typeof normalized.invoice_number !== 'string' || !normalized.invoice_number.trim()) {
    throw new NormalizationError('MISSING_INVOICE_NUMBER', 'invoice_number is required');
  }
  normalized.invoice_number = normalized.invoice_number.trim();
  if (typeof normalized.sender === 'string') normalized.sender = normalized.sender.trim();
  if (!normalized.document_type) normalized.document_type = 'invoice';
  if (!normalized.status) normalized.status = 'EXTRACTED';
  if (normalized.confidence !== null && normalized.confidence !== undefined) {
    const confidence = Number(normalized.confidence);
    if (typeof normalized.confidence === 'boolean' || !Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      throw new NormalizationError('INVALID_CONFIDENCE', 'confidence must be between 0 and 1');
    }
    normalized.confidence = confidence;
  }
  return { invoice_data: normalized, payload_fingerprint: sha256Hex(canonicalJson(normalized)) };
}

module.exports = { STORED_FIELDS, NormalizationError, amountText, normalizeForStorage };
