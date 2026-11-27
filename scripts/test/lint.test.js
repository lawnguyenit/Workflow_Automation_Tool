'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { readJson } = require('../lib/common');
const { normalizeAll } = require('../lib/normalize');
const { lintWorkflows, isDefaultName, scanSecrets } = require('../lib/lint');

const raw = ['raw-sub.json', 'raw-main.json'].map((f) => readJson(path.join(__dirname, 'fixtures', f)));
const [subN, mainN] = normalizeAll(raw).map((r) => r.workflow);
const good = () => [{ rel: 'sub/normalize-lead', wf: structuredClone(subN) }, { rel: 'lead-intake', wf: structuredClone(mainN) }];
const checks = (ps) => ps.map((p) => p.check);

test('clean fixtures pass', () => assert.deepEqual(lintWorkflows(good()), []));

test('default node names are flagged', () => {
  for (const n of ['HTTP Request', 'HTTP Request1', 'Code', 'IF', 'If2', 'Edit Fields', 'Switch', 'Merge1']) {
    assert.ok(isDefaultName({ name: n, type: 'n8n-nodes-base.set' }), n);
  }
  assert.ok(isDefaultName({ name: 'httpRequest', type: 'n8n-nodes-base.httpRequest' }));
  assert.ok(!isDefaultName({ name: 'Post to CRM', type: 'n8n-nodes-base.httpRequest' }));
  assert.ok(!isDefaultName({ name: 'Code', type: 'n8n-nodes-base.stickyNote' }));
  const items = good();
  items[1].wf.nodes[0].name = 'Code';
  assert.ok(checks(lintWorkflows(items)).includes('node-names'));
});

test('sub-workflow must declare inputs', () => {
  let items = good();
  items[0].wf.nodes.find((n) => n.type.endsWith('executeWorkflowTrigger')).parameters = { inputSource: 'passthrough' };
  assert.ok(checks(lintWorkflows(items)).includes('sub-inputs'));
  items = good();
  items[0].wf.nodes.find((n) => n.type.endsWith('executeWorkflowTrigger')).parameters = { inputSource: 'workflowInputs', workflowInputs: { values: [] } };
  assert.ok(checks(lintWorkflows(items)).includes('sub-inputs'));
  items = good();
  items[0].wf.nodes = items[0].wf.nodes.filter((n) => !n.type.endsWith('executeWorkflowTrigger'));
  assert.ok(checks(lintWorkflows(items)).includes('sub-inputs'));
  items = good();
  items[0].wf.nodes.find((n) => n.type.endsWith('executeWorkflowTrigger')).parameters = { inputSource: 'jsonExample', jsonExample: '{"a":1}' };
  assert.deepEqual(lintWorkflows(items), []);
});

test('unknown reference and misplacement are flagged', () => {
  const items = good();
  items[1].wf.settings.errorWorkflow = '@workflow:does not exist';
  assert.ok(checks(lintWorkflows(items)).includes('references'));
  const mis = good();
  mis[1].rel = 'sub/lead-intake';
  assert.ok(checks(lintWorkflows(mis)).includes('placement'));
});

test('secret scanner: finds real-looking secrets, ignores expressions and example domains', () => {
  const mk = (params) => ({ name: 'w', nodes: [{ name: 'n', type: 'n8n-nodes-base.httpRequest', parameters: params }] });
  const hit = (params) => scanSecrets(mk(params)).map((h) => h.what);
  assert.ok(hit({ headers: { parameters: [{ name: 'Authorization', value: 'Bearer abcdefghijklmnopqrstuvwx' }] } }).length);
  assert.ok(hit({ key: 'sk-abcdefghijklmnopqrstuvwxyz123456' }).includes('OpenAI/Anthropic-style key'));
  assert.ok(hit({ a: 'ghp_abcdefghijklmnopqrstuvwxyz0123456789' }).includes('GitHub token'));
  assert.ok(hit({ a: 'AKIAABCDEFGHIJKLMNOP' }).includes('AWS access key'));
  assert.ok(hit({ a: 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghij' }).includes('JWT'));
  assert.ok(hit({ to: 'jane.doe@gmail.com' }).includes('email address'));
  assert.ok(hit({ phone: '+84 912 345 678' }).includes('Phone number'));
  assert.ok(hit({ phone: '(415) 867-5309' }).includes('Phone number'));
  assert.ok(hit({ apiKey: 'a-very-long-literal-value-123' }).length);
  // negatives
  assert.deepEqual(hit({ phone: '+15550102030' }), []);
  assert.deepEqual(hit({ headers: { parameters: [{ name: 'Authorization', value: '=Bearer {{ $env.TOKEN }}' }] } }), []);
  assert.deepEqual(hit({ to: 'someone@example.com', b: 'ops@staging.example.org' }), []);
  assert.deepEqual(hit({ apiKey: '={{ $env.API_KEY }}' }), []);
  assert.deepEqual(hit({ type: '@n8n/n8n-nodes-langchain.agent', id: '1234567890123456', ts: '2026-11-27T10:00:00.000Z' }), []);
  assert.deepEqual(hit({ sheet: '1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms' }), []);
});

test('allowlist suppresses exact values', () => {
  const wf = { name: 'w', nodes: [{ name: 'n', type: 'n8n-nodes-base.set', parameters: { to: 'jane.doe@gmail.com' } }] };
  assert.equal(scanSecrets(wf, new Set(['jane.doe@gmail.com'])).length, 0);
});

test('findings never print the full secret', () => {
  const wf = { name: 'w', nodes: [{ name: 'n', type: 'n8n-nodes-base.set', parameters: { k: 'sk-abcdefghijklmnopqrstuvwxyz123456' } }] };
  const [h] = scanSecrets(wf);
  assert.ok(!h.sample.includes('abcdefghijklmnop'));
});
