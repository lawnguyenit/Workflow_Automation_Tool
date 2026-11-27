'use strict';
// Deterministic JSON: object keys sorted, no whitespace, non-ASCII kept as-is.
// Matches Python json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)
// for strings, booleans, null, arrays, objects and integers. Floats may differ
// (Python prints 100.0, JavaScript prints 100), so fingerprints are JS-only.

function canonicalJson(value) {
  if (value === null || typeof value !== 'object') {
    if (typeof value === 'number' && !Number.isFinite(value)) throw new TypeError('non-finite number');
    if (value === undefined) return 'null';
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  const keys = Object.keys(value).filter((k) => value[k] !== undefined).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k])}`).join(',')}}`;
}

module.exports = { canonicalJson };
