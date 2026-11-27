'use strict';
// Structural checks on the generated n8n workflow files.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { WF, outputs } = require('../tools/build-workflows');

const ROOT = path.resolve(__dirname, '..', '..');
const load = (w) => JSON.parse(fs.readFileSync(path.join(ROOT, 'workflows', w.dir, 'workflow.json'), 'utf8'));
const all = Object.fromEntries(Object.entries(WF).map(([k, w]) => [k, load(w)]));
const byName = Object.fromEntries(Object.values(all).map((wf) => [wf.name, wf]));
const REF = '@workflow:';
const realNodes = (wf) => wf.nodes.filter((n) => n.type !== 'n8n-nodes-base.stickyNote');

// Node types and versions confirmed present in n8n 2.49.8 (n8n-nodes-base/dist/nodes).
const KNOWN = {
  'n8n-nodes-base.code': [1, 2],
  'n8n-nodes-base.set': [3, 3.1, 3.2, 3.3, 3.4, 3.5],
  'n8n-nodes-base.if': [2, 2.1, 2.2, 2.3, 2.4],
  'n8n-nodes-base.switch': [3, 3.1, 3.2, 3.3, 3.4, 3.5],
  'n8n-nodes-base.postgres': [2, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8],
  'n8n-nodes-base.httpRequest': [3, 4, 4.1, 4.2, 4.3, 4.4, 4.5],
  'n8n-nodes-base.gmail': [2, 2.1, 2.2],
  'n8n-nodes-base.gmailTrigger': [1, 1.1, 1.2, 1.3, 1.4],
  'n8n-nodes-base.executeWorkflow': [1, 1.1, 1.2, 1.3, 1.4],
  'n8n-nodes-base.executeWorkflowTrigger': [1, 1.1, 1.2, 1.3],
  'n8n-nodes-base.extractFromFile': [1, 1.1, 1.2],
  'n8n-nodes-base.crypto': [1, 2],
  'n8n-nodes-base.errorTrigger': [1],
  'n8n-nodes-base.stickyNote': [1],
};

test('committed workflow files match the generator output (normalized)', (t) => {
  const files = outputs();
  if (!files) return t.skip('scripts/lib/normalize.js not found (set N8N_TOOLING_DIR before the tooling merge)');
  for (const [file, content] of files) {
    assert.equal(fs.readFileSync(file, 'utf8'), content, `${path.relative(ROOT, file)} is stale: run node code/tools/build-workflows.js`);
  }
});

test('every node uses a known type/version and a descriptive unique name; sub names start with "sub: "', () => {
  for (const key of ['checkDuplicate', 'extract', 'validate', 'store', 'notify']) assert.match(all[key].name, /^sub: /);
  for (const wf of Object.values(all)) {
    const names = wf.nodes.map((n) => n.name);
    assert.equal(new Set(names).size, names.length, `${wf.name}: duplicate node names`);
    for (const n of wf.nodes) {
      assert.ok(KNOWN[n.type], `${wf.name}/${n.name}: unknown type ${n.type}`);
      assert.ok(KNOWN[n.type].includes(n.typeVersion), `${wf.name}/${n.name}: ${n.type} v${n.typeVersion} not in n8n 2.49.8`);
      assert.doesNotMatch(n.name, /^(Code|Set|If|Switch|Postgres|HTTP Request|Gmail|Edit Fields|Execute Workflow)\d*$/, `${wf.name}: default node name ${n.name}`);
    }
  }
});

test('connections only point at existing nodes', () => {
  for (const wf of Object.values(all)) {
    const names = new Set(wf.nodes.map((n) => n.name));
    for (const [from, out] of Object.entries(wf.connections)) {
      assert.ok(names.has(from), `${wf.name}: connection from missing ${from}`);
      for (const branch of out.main) for (const link of branch) assert.ok(names.has(link.node), `${wf.name}: link to missing ${link.node}`);
    }
  }
});

test('error workflow is set everywhere except on the error handler itself', () => {
  for (const [key, wf] of Object.entries(all)) {
    if (key === 'errors') assert.equal(wf.settings.errorWorkflow, undefined);
    else assert.equal(wf.settings.errorWorkflow, REF + WF.errors.name, wf.name);
    assert.ok(wf.active === undefined || wf.active === false);
    assert.equal(wf.settings.executionOrder, 'v1');
  }
  assert.ok(all.errors.nodes.some((n) => n.type === 'n8n-nodes-base.errorTrigger'));
});

test('credentials are name placeholders only', () => {
  const text = Object.values(all).map((wf) => JSON.stringify(wf)).join('\n');
  assert.doesNotMatch(text, /AIza[0-9A-Za-z_-]{20,}|ya29\.|-----BEGIN|"password"\s*:/);
  for (const wf of Object.values(all)) {
    for (const n of wf.nodes) {
      for (const ref of Object.values(n.credentials ?? {})) {
        assert.deepEqual(Object.keys(ref), ['name']);
        assert.ok(['Gmail (secondary account)', 'Gemini API', 'Postgres (local)'].includes(ref.name), ref.name);
      }
    }
  }
});

test('each sub-workflow declares typed inputs and each call maps exactly those inputs', () => {
  const subs = ['checkDuplicate', 'extract', 'validate', 'store', 'notify'];
  const declared = {};
  for (const key of subs) {
    const trigger = all[key].nodes.filter((n) => n.type === 'n8n-nodes-base.executeWorkflowTrigger');
    assert.equal(trigger.length, 1, all[key].name);
    assert.equal(trigger[0].parameters.inputSource, 'workflowInputs');
    const values = trigger[0].parameters.workflowInputs.values;
    assert.ok(values.length > 0 && values.every((v) => v.name && v.type), all[key].name);
    declared[all[key].name] = values;
  }
  const calls = Object.values(all).flatMap((wf) => wf.nodes.filter((n) => n.type === 'n8n-nodes-base.executeWorkflow'));
  assert.equal(calls.length, 5);
  for (const call of calls) {
    assert.ok(call.parameters.workflowId.value.startsWith(REF), call.name);
    const target = call.parameters.workflowId.value.slice(REF.length);
    assert.ok(byName[target], `${call.name}: unknown target ${target}`);
    const want = declared[target].map((v) => `${v.name}:${v.type}`).sort();
    const got = call.parameters.workflowInputs.schema.map((s) => `${s.id}:${s.type}`).sort();
    assert.deepEqual(got, want, call.name);
    assert.deepEqual(Object.keys(call.parameters.workflowInputs.value).sort(), declared[target].map((v) => v.name).sort());
  }
});

test('orchestrator holds no business rules: no embedded code/src modules, no Postgres, no HTTP', () => {
  for (const n of all.intake.nodes) {
    assert.ok(!['n8n-nodes-base.postgres', 'n8n-nodes-base.httpRequest'].includes(n.type), n.name);
    if (n.type === 'n8n-nodes-base.code') assert.doesNotMatch(n.parameters.jsCode, /Embedded from code\/src/, n.name);
  }
  assert.ok(all.intake.nodes.filter((n) => n.type === 'n8n-nodes-base.stickyNote').length >= 4);
});

test('every Code node is syntactically valid JavaScript', () => {
  for (const wf of Object.values(all)) {
    for (const n of realNodes(wf).filter((x) => x.type === 'n8n-nodes-base.code')) {
      assert.doesNotThrow(() => new Function('$input', '$', '$json', '$execution', n.parameters.jsCode), `${wf.name}/${n.name}`);
    }
  }
});

test('validate-invoice Code node runs on the embedded rules', () => {
  const n = all.validate.nodes.find((x) => x.name === 'Apply validation rules');
  const run = new Function('$input', n.parameters.jsCode);
  const invoice = {
    record_key: 'invoice:x', document_type: 'invoice', sender: 'Example Co', invoice_number: 'INV-1', amount: 10,
    currency: 'usd', status: 'EXTRACTED', confidence: 0.9, missing_fields: [], conflicts: [],
  };
  const out = run({ all: () => [{ json: { invoice } }, { json: { invoice: { ...invoice, amount: -5 } } }] });
  assert.equal(out[0].json.valid, true);
  assert.equal(out[0].json.invoice.currency, 'USD');
  assert.equal(out[1].json.valid, false);
  assert.ok(out[1].json.issues.some((i) => i.code === 'AMOUNT_BELOW_MINIMUM'));
});

test('extract-invoice: cap reservation happens before the Gemini call and every branch ends in one return node', () => {
  const c = all.extract.connections;
  assert.deepEqual(c['Extraction settings'].main[0].map((l) => l.node), ['Reserve one AI call for today']);
  assert.deepEqual(c['Under daily cap?'].main[0].map((l) => l.node), ['Build Gemini request']);
  const ends = Object.entries(c).filter(([, o]) => o.main.some((b) => b.some((l) => l.node === 'Return extraction result'))).map(([k]) => k);
  assert.equal(ends.length, 5);
  const http = all.extract.nodes.find((n) => n.type === 'n8n-nodes-base.httpRequest');
  assert.equal(http.parameters.nodeCredentialType, 'googlePalmApi');
  assert.match(http.parameters.url, /generativelanguage\.googleapis\.com\/v1beta\/models\/.*:generateContent/);
});

test('notify Compose Code node builds subject and body from issue codes only', () => {
  const n = all.notify.nodes.find((x) => x.name === 'Compose notification text');
  const run = new Function('$input', '$execution', n.parameters.jsCode);
  const out = run({ all: () => [{ json: { notify_to: 'ops@example.invalid', status: 'REVIEW_REQUIRED', filename: 'a.pdf', invoice_number: null, sender: null, amount: null, currency: null, invoice_id: null, issues: ['DAILY_AI_CAP_REACHED', 'EXTRACTION_REQUIRES_REVIEW', 'EXTRACTION_REQUIRES_REVIEW'] } }] }, { id: '7' });
  assert.equal(out[0].json.subject, 'Invoice needs manual review');
  assert.match(out[0].json.body, /Issues: DAILY_AI_CAP_REACHED, EXTRACTION_REQUIRES_REVIEW\n/);
  assert.match(out[0].json.body, /n8n execution: 7/);
});

test('orchestrator re-attaches the PDF binary from the split node (Crypto hash output has no binary)', () => {
  const n = all.intake.nodes.find((x) => x.name === 'Attach duplicate result to PDF');
  assert.match(n.parameters.jsCode, /\$\('Split one item per PDF attachment'\)\.itemMatching\(i\)/);
  assert.match(n.parameters.jsCode, /binary: split\.binary/);
});
