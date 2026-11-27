'use strict';
// Repository checks for workflows/**/workflow.json. Pure functions; CLI in ../n8n-lint.js.
const path = require('node:path');
const { isSubWorkflow, workflowRelDir } = require('./common');
const { referencedWorkflowNames } = require('./normalize');
const { shortType } = require('./diagram');

// Names n8n assigns by default (optionally with a numeric suffix such as "HTTP Request1").
const DEFAULT_NAMES = [
  'HTTP Request', 'Code', 'IF', 'Switch', 'Merge', 'Set', 'Edit Fields', 'Edit Fields (Set)', 'Webhook', 'Wait',
  'Filter', 'Loop Over Items', 'Loop Over Items (Split in Batches)', 'No Operation, do nothing', 'Execute Workflow',
  'Execute Sub-workflow', 'Respond to Webhook', 'Schedule Trigger', 'Manual Trigger', 'Error Trigger', 'Sort',
  'Aggregate', 'Split Out', 'Limit', 'Remove Duplicates', 'Summarize', 'Stop and Error', 'Date & Time', 'Crypto',
  'Postgres', 'Gmail', 'Google Sheets', 'Slack', 'OpenAI', 'Function', 'Function Item', 'Start',
  'When Executed by Another Workflow', 'When clicking ‘Execute workflow’', "When clicking 'Test workflow'",
  'When clicking ‘Test workflow’', 'Execute Command', 'Read/Write Files from Disk', 'Compare Datasets', 'Rename Keys',
  'Convert to File', 'Extract From File', 'Respond to Chat', 'Item Lists', 'RSS Read', 'XML', 'HTML', 'Markdown',
  'Form Trigger', 'On form submission', 'Webhook1',
].map((s) => s.toLowerCase());

const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '');

function isDefaultName(node) {
  if (/stickyNote$/.test(node.type)) return false;
  const base = String(node.name).replace(/\s*\d+$/, '').trim().toLowerCase();
  if (DEFAULT_NAMES.includes(base)) return true;
  // Name equals the node type's own identifier, e.g. "httpRequest" / "Http Request".
  const t = norm(shortType(node.type).replace(/^ai\./, ''));
  return norm(base) === t || norm(base) === t.replace(/trigger$/, '') + 'trigger';
}

const EMAIL_ALLOWED_DOMAIN = /(^|\.)(example\.(com|org|net)|example|test|invalid|localhost|local|users\.noreply\.github\.com)$/i;
const SECRET_PATTERNS = [
  ['OpenAI/Anthropic-style key', /\bsk-[A-Za-z0-9_-]{20,}/],
  ['GitHub token', /\b(ghp|gho|ghs|ghu)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}/],
  ['Slack token', /\bxox[abprs]-[A-Za-z0-9-]{10,}/],
  ['AWS access key', /\bAKIA[0-9A-Z]{16}\b/],
  ['Google API key', /\bAIza[0-9A-Za-z_-]{35}\b/],
  ['JWT', /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}/],
  ['Bearer token', /\bBearer\s+(?![{$=])[A-Za-z0-9._~+/-]{16,}=*/],
  ['Private key', /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
  ['Phone number', /(?<![\w.])(\+\d{1,3}[\s.-]?\(?\d{1,4}\)?[\s.-]?\d{3}[\s.-]?\d{3,4}\b|\(\d{3}\)\s?\d{3}[\s.-]\d{4}\b|\b0\d{2,3}[\s.-]\d{3}[\s.-]\d{3,4}\b)/],
];
const EMAIL_RE = /[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})/g;
const SECRET_KEY_RE = /^(api[_-]?key|apikey|secret|password|passwd|access[_-]?token|auth[_-]?token|client[_-]?secret|private[_-]?key|authorization|x-api-key)$/i;

function isExpression(s) {
  return s.startsWith('=') || /\{\{.*\}\}/.test(s) || /\$env\b|\$json\b|\$\(/.test(s);
}

function mask(s) {
  return s.length <= 6 ? '***' : `${s.slice(0, 3)}***(${s.length} chars)`;
}

/** Yield [path, string] for every string value. */
function* walkStrings(value, p = '') {
  if (typeof value === 'string') yield [p, value];
  else if (Array.isArray(value)) for (let i = 0; i < value.length; i++) yield* walkStrings(value[i], `${p}[${i}]`);
  else if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) yield* walkStrings(v, p ? `${p}.${k}` : k);
  }
}

function scanSecrets(wf, allow = new Set()) {
  const hits = [];
  const strings = [...walkStrings(wf.nodes || [], 'nodes')];
  // Header-style {name: "Authorization", value: "..."} pairs.
  const pairs = [];
  (function findPairs(v, p) {
    if (Array.isArray(v)) v.forEach((x, i) => findPairs(x, `${p}[${i}]`));
    else if (v && typeof v === 'object') {
      if (typeof v.name === 'string' && typeof v.value === 'string' && SECRET_KEY_RE.test(v.name)) pairs.push([`${p}.value`, v.value]);
      for (const [k, x] of Object.entries(v)) findPairs(x, `${p}.${k}`);
    }
  })(wf.nodes || [], 'nodes');
  for (const [p, s] of pairs) {
    if (s.length >= 8 && !isExpression(s) && !allow.has(s)) hits.push({ path: p, what: 'literal value for secret-like header/field', sample: mask(s) });
  }
  for (const [p, s] of strings) {
    if (allow.has(s)) continue;
    const key = p.split('.').pop().replace(/\[\d+\]$/, '');
    if (SECRET_KEY_RE.test(key) && s.length >= 12 && !isExpression(s) && !/^[\w-]*(replace|change|your|example|placeholder|xxx)/i.test(s)) {
      hits.push({ path: p, what: `literal value under secret-like key "${key}"`, sample: mask(s) });
    }
    for (const [what, re] of SECRET_PATTERNS) {
      const m = re.exec(s);
      // 555-01xx is the NANP range reserved for fictional numbers.
      if (what === 'Phone number' && m && /555[\s.-]?01\d\d/.test(m[0])) continue;
      if (m && !allow.has(m[0])) hits.push({ path: p, what, sample: mask(m[0]) });
    }
    for (const m of s.matchAll(EMAIL_RE)) {
      if (!EMAIL_ALLOWED_DOMAIN.test(m[1]) && !allow.has(m[0])) hits.push({ path: p, what: 'email address', sample: mask(m[0]) });
    }
  }
  return hits;
}

/**
 * @param {{rel: string, wf: object}[]} items rel = dir relative to workflows root (posix)
 * @param {{allow?: Set<string>}} [opts]
 * @returns {{file: string, check: string, message: string}[]}
 */
function lintWorkflows(items, opts = {}) {
  const allow = opts.allow || new Set();
  const problems = [];
  const add = (rel, check, message) => problems.push({ file: `${rel}/workflow.json`, check, message });
  const names = new Map(items.map((i) => [i.wf.name, i.rel]));
  const seen = new Map();
  for (const { rel, wf } of items) {
    if (seen.has(wf.name)) add(rel, 'unique-name', `duplicate workflow name ${JSON.stringify(wf.name)} (also in ${seen.get(wf.name)})`);
    seen.set(wf.name, rel);
    let expectedRel;
    try { expectedRel = workflowRelDir(wf); } catch (e) { add(rel, 'placement', e.message); }
    if (expectedRel && expectedRel !== rel) add(rel, 'placement', `workflow ${JSON.stringify(wf.name)} belongs in workflows/${expectedRel}/`);

    for (const n of wf.nodes || []) {
      if (isDefaultName(n)) add(rel, 'node-names', `node ${JSON.stringify(n.name)} (${shortType(n.type)}) has a default name; give it a descriptive name`);
    }
    if (isSubWorkflow(wf)) {
      const trig = (wf.nodes || []).find((n) => shortType(n.type) === 'executeWorkflowTrigger');
      if (!trig) add(rel, 'sub-inputs', 'sub-workflow has no Execute Workflow Trigger node');
      else {
        const p = trig.parameters || {};
        const src = p.inputSource || 'workflowInputs';
        if (src === 'workflowInputs') {
          const vals = (p.workflowInputs && p.workflowInputs.values) || [];
          if (!vals.length || vals.some((v) => !v.name)) add(rel, 'sub-inputs', 'sub-workflow trigger declares no (or unnamed) input fields');
        } else if (src === 'jsonExample') {
          if (!String(p.jsonExample || '').trim()) add(rel, 'sub-inputs', 'sub-workflow trigger has empty JSON example');
        } else add(rel, 'sub-inputs', `sub-workflow trigger uses inputSource "${src}" (inputs not declared)`);
      }
    }
    for (const r of referencedWorkflowNames(wf)) {
      if (!names.has(r.name)) add(rel, 'references', `${r.kind === 'call' ? `node ${JSON.stringify(r.node)}` : 'settings.errorWorkflow'} references unknown workflow ${JSON.stringify(r.name)}`);
    }
    for (const h of scanSecrets(wf, allow)) add(rel, 'secrets', `${h.what} at ${h.path}: ${h.sample}`);
    if (wf.settings && wf.settings.errorWorkflow === undefined) { /* optional */ }
  }
  return problems;
}

module.exports = { lintWorkflows, scanSecrets, isDefaultName };
