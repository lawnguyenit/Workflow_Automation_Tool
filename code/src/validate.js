'use strict';
// Invoice validation rules, ported from legacy/python-modules/src/workflow_automation/
// modules/processing/validate.py (class Validate). Rule codes and order are kept.
// Differences from Python are listed in docs/architecture.md ("Ported rules").

const INVOICE_FIELDS = ['document_type', 'sender', 'invoice_number', 'amount', 'currency'];
const DEFAULT_ALLOWED_FIELDS = [
  'record_key', ...INVOICE_FIELDS, 'source_ids', 'filename', 'extraction_provider',
  'extraction_model', 'source_system', 'source_account', 'source_message_id',
  'source_attachment_id', 'status', 'confidence', 'missing_fields', 'conflicts', 'error_code',
];

function isFiniteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

function buildConfig(options = {}) {
  const required = options.required_fields ?? INVOICE_FIELDS;
  const unknown = required.filter((f) => !INVOICE_FIELDS.includes(f));
  if (unknown.length) throw new Error(`Unsupported required fields: ${[...new Set(unknown)].sort().join(', ')}`);
  const threshold = (value, name) => {
    if (value === null || value === undefined) return null;
    if (!isFiniteNumber(value)) throw new Error(`${name} must be a finite number`);
    return value;
  };
  const minimum = threshold(options.minimum_amount === undefined ? 0 : options.minimum_amount, 'minimum_amount');
  const maximum = threshold(options.maximum_amount, 'maximum_amount');
  if (minimum !== null && maximum !== null && minimum > maximum) throw new Error('minimum_amount cannot exceed maximum_amount');
  const minConf = options.minimum_confidence ?? null;
  if (minConf !== null && !(isFiniteNumber(minConf) && minConf >= 0 && minConf <= 1)) {
    throw new Error('minimum_confidence must be between 0 and 1');
  }
  const allowed = new Set(options.allowed_fields ?? DEFAULT_ALLOWED_FIELDS);
  if (!allowed.has('record_key')) throw new Error('allowed_fields must include record_key');
  return {
    requiredFields: [...new Set(required)],
    allowedCurrencies: options.allowed_currencies == null ? null : new Set(options.allowed_currencies.map((c) => String(c).toUpperCase())),
    minimumAmount: minimum,
    maximumAmount: maximum,
    minimumConfidence: minConf,
    allowedFields: allowed,
  };
}

function uniqueIssues(issues) {
  const seen = new Set();
  return issues.filter((issue) => {
    const key = `${issue.code}\u0000${issue.field}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function validateRecord(record, config) {
  const issues = [];
  for (const field of Object.keys(record)) {
    if (!config.allowedFields.has(field)) issues.push({ code: 'UNSUPPORTED_FIELD', field });
  }
  if (record.status !== 'EXTRACTED') issues.push({ code: 'EXTRACTION_REQUIRES_REVIEW', field: 'status' });

  for (const field of config.requiredFields) {
    const value = record[field];
    if (value === null || value === undefined || (typeof value === 'string' && !value.trim())) {
      issues.push({ code: `MISSING_${field.toUpperCase()}`, field });
    }
  }
  for (const field of ['document_type', 'sender', 'invoice_number']) {
    const value = record[field];
    if (value !== null && value !== undefined && (typeof value !== 'string' || !value.trim())) {
      issues.push({ code: `INVALID_${field.toUpperCase()}`, field });
    }
  }

  const amount = record.amount;
  if (amount !== null && amount !== undefined) {
    if (!isFiniteNumber(amount)) {
      issues.push({ code: 'INVALID_AMOUNT', field: 'amount' });
    } else {
      if (config.minimumAmount !== null && amount < config.minimumAmount) issues.push({ code: 'AMOUNT_BELOW_MINIMUM', field: 'amount' });
      if (config.maximumAmount !== null && amount > config.maximumAmount) issues.push({ code: 'AMOUNT_ABOVE_MAXIMUM', field: 'amount' });
    }
  }

  const currency = record.currency;
  if (currency !== null && currency !== undefined) {
    if (typeof currency !== 'string' || !/^[A-Za-z]{3}$/.test(currency.trim())) {
      issues.push({ code: 'INVALID_CURRENCY', field: 'currency' });
    } else if (config.allowedCurrencies !== null && !config.allowedCurrencies.has(currency.trim().toUpperCase())) {
      issues.push({ code: 'UNSUPPORTED_CURRENCY', field: 'currency' });
    }
  }

  const confidence = record.confidence;
  if (confidence !== null && confidence !== undefined) {
    if (!isFiniteNumber(confidence) || confidence < 0 || confidence > 1) {
      issues.push({ code: 'INVALID_CONFIDENCE', field: 'confidence' });
    } else if (config.minimumConfidence !== null && confidence < config.minimumConfidence) {
      issues.push({ code: 'CONFIDENCE_BELOW_MINIMUM', field: 'confidence' });
    }
  } else if (config.minimumConfidence !== null) {
    issues.push({ code: 'MISSING_CONFIDENCE', field: 'confidence' });
  }

  const sourceIds = record.source_ids;
  if (sourceIds !== null && sourceIds !== undefined && (!Array.isArray(sourceIds) || sourceIds.some((s) => typeof s !== 'string'))) {
    issues.push({ code: 'INVALID_SOURCE_IDS', field: 'source_ids' });
  }
  return uniqueIssues(issues);
}

function normalizeAccepted(record) {
  const normalized = { ...record };
  if (typeof normalized.currency === 'string') normalized.currency = normalized.currency.trim().toUpperCase();
  return normalized;
}

function sourceIdsOf(value) {
  return Array.isArray(value) ? value.filter((s) => typeof s === 'string') : [];
}

/**
 * Validate a list of extracted invoice records.
 * Returns { status: SUCCESS|PARTIAL|REVIEW_REQUIRED|FAILURE, records, rejected, errors }.
 */
function validateRecords(records, options = {}) {
  const config = buildConfig(options);
  if (!Array.isArray(records)) {
    return { status: 'FAILURE', records: [], rejected: [], errors: [{ code: 'INVALID_RECORDS', field: null }] };
  }
  const accepted = [];
  const rejected = [];
  const errors = [];
  const seenKeys = new Set();
  records.forEach((record, index) => {
    if (record === null || typeof record !== 'object' || Array.isArray(record)) {
      const itemErrors = [{ code: 'INVALID_RECORD', field: null }];
      rejected.push({ record_key: null, source_ids: [], errors: itemErrors, record });
      errors.push({ code: 'INVALID_RECORD', field: null, index });
      return;
    }
    const key = record.record_key;
    let itemErrors;
    if (typeof key !== 'string' || !key.trim()) itemErrors = [{ code: 'MISSING_RECORD_KEY', field: 'record_key' }];
    else if (seenKeys.has(key)) itemErrors = [{ code: 'DUPLICATE_RECORD_KEY', field: 'record_key' }];
    else {
      seenKeys.add(key);
      itemErrors = validateRecord(record, config);
    }
    if (itemErrors.length) {
      rejected.push({ record_key: typeof key === 'string' ? key : null, source_ids: sourceIdsOf(record.source_ids), errors: itemErrors, record });
      for (const issue of itemErrors) errors.push({ ...issue, index });
    } else {
      accepted.push(normalizeAccepted(record));
    }
  });
  let status = 'SUCCESS';
  if (rejected.length && accepted.length) status = 'PARTIAL';
  else if (rejected.length) status = 'REVIEW_REQUIRED';
  return { status, records: accepted, rejected, errors };
}

/** Single-invoice form used by the validate-invoice sub-workflow. */
function validateInvoice(record, options = {}) {
  const result = validateRecords([record], options);
  const rejected = result.rejected[0];
  return {
    valid: result.status === 'SUCCESS',
    status: result.status === 'SUCCESS' ? 'VALID' : 'REVIEW_REQUIRED',
    invoice: result.status === 'SUCCESS' ? result.records[0] : null,
    issues: rejected ? rejected.errors : [],
  };
}

module.exports = { INVOICE_FIELDS, DEFAULT_ALLOWED_FIELDS, buildConfig, validateRecord, validateRecords, validateInvoice };
