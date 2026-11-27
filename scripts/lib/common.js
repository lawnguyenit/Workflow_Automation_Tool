'use strict';
// Shared helpers for the n8n-as-code tooling. No external dependencies.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const SUB_PREFIX_RE = /^sub:\s*/i;
const SUB_TAG = 'sub';

/** Lowercase ASCII slug; Vietnamese diacritics are folded. */
function slugify(text) {
  return String(text)
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/đ/gi, 'd')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function tagNames(wf) {
  return (wf.tags || []).map((t) => (typeof t === 'string' ? t : t && t.name)).filter(Boolean);
}

/** A workflow is a sub-workflow when its name starts with "sub: " or it carries the tag "sub". */
function isSubWorkflow(wf) {
  return SUB_PREFIX_RE.test(wf.name || '') || tagNames(wf).map((t) => t.toLowerCase()).includes(SUB_TAG);
}

/** Directory (relative to the workflows root) that holds a workflow: <slug> or sub/<slug>. */
function workflowRelDir(wf) {
  const base = isSubWorkflow(wf) ? (wf.name || '').replace(SUB_PREFIX_RE, '') : wf.name || '';
  const slug = slugify(base);
  if (!slug) throw new Error(`Workflow has no usable name: ${JSON.stringify(wf.name)}`);
  return isSubWorkflow(wf) ? path.posix.join('sub', slug) : slug;
}

/** Recursively sort object keys; arrays keep their order. */
function sortKeys(value) {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === 'object') {
    const out = {};
    for (const k of Object.keys(value).sort()) out[k] = sortKeys(value[k]);
    return out;
  }
  return value;
}

/** Stable pretty JSON with 2-space indent and trailing newline. */
function stringify(value) {
  return JSON.stringify(value, null, 2) + '\n';
}

/** Deterministic UUID-shaped id from a string (sha1-based, v5-like). */
function deterministicUuid(seed) {
  const h = crypto.createHash('sha1').update(String(seed)).digest();
  h[6] = (h[6] & 0x0f) | 0x50;
  h[8] = (h[8] & 0x3f) | 0x80;
  const x = h.subarray(0, 16).toString('hex');
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20, 32)}`;
}

/** Deterministic 16-char alphanumeric workflow id (the shape n8n itself uses). */
function deterministicWorkflowId(name) {
  const alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  const h = crypto.createHash('sha256').update(`workflow:${name}`).digest();
  let out = '';
  for (let i = 0; i < 16; i++) out += alphabet[h[i] % alphabet.length];
  return out;
}

/** Find every workflow.json under root (workflows/<slug>/workflow.json and workflows/sub/<slug>/workflow.json). */
function findWorkflowFiles(root) {
  const out = [];
  const walk = (dir) => {
    if (!fs.existsSync(dir)) return;
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name === 'workflow.json') out.push(p);
    }
  };
  walk(root);
  return out.sort();
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    throw new Error(`Invalid JSON in ${file}: ${e.message}`);
  }
}

/** Minimal argv parser: --flag, --key=value, --key value (for keys in `valued`), positionals. */
function parseArgs(argv, valued = []) {
  const opts = {};
  const positional = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const eq = a.indexOf('=');
      if (eq > -1) opts[a.slice(2, eq)] = a.slice(eq + 1);
      else if (valued.includes(a.slice(2))) opts[a.slice(2)] = argv[++i];
      else opts[a.slice(2)] = true;
    } else positional.push(a);
  }
  return { opts, positional };
}

module.exports = {
  slugify, tagNames, isSubWorkflow, workflowRelDir, sortKeys, stringify, deterministicUuid,
  deterministicWorkflowId, findWorkflowFiles, readJson, parseArgs, SUB_PREFIX_RE,
};
