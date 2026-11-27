'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { readJson, stringify, workflowRelDir, slugify, isSubWorkflow } = require('../lib/common');
const { normalizeAll, normalizeWorkflow, denormalizeWorkflow, buildIdToName, referencedWorkflowNames } = require('../lib/normalize');

const raw = ['raw-sub.json', 'raw-main.json'].map((f) => readJson(path.join(__dirname, 'fixtures', f)));
const [subN, mainN] = normalizeAll(raw).map((r) => r.workflow);

test('slug and placement', () => {
  assert.equal(slugify('Lead Qualification – Đơn hàng'), 'lead-qualification-don-hang');
  assert.equal(workflowRelDir(subN), 'sub/normalize-lead');
  assert.equal(workflowRelDir(mainN), 'lead-intake');
  assert.ok(isSubWorkflow({ name: 'Foo', tags: [{ name: 'sub' }] }));
});

test('volatile fields are removed', () => {
  for (const w of [subN, mainN]) {
    for (const k of ['id', 'versionId', 'meta', 'pinData', 'updatedAt', 'createdAt', 'active', 'staticData', 'triggerCount', 'isArchived']) {
      assert.ok(!(k in w), `${k} should be gone`);
    }
    for (const n of w.nodes) assert.ok(!('webhookId' in n));
  }
  assert.deepEqual(subN.tags, ['sub']);
});

test('node ids deterministic and independent of original ids', () => {
  const again = normalizeAll(JSON.parse(JSON.stringify(raw)).map((w) => ({ ...w, nodes: w.nodes.map((n) => ({ ...n, id: 'zzz-' + n.name })) })));
  assert.deepEqual(again.map((r) => r.workflow), [subN, mainN]);
  assert.match(mainN.nodes[0].id, /^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test('credential ids dropped, names kept', () => {
  const crm = mainN.nodes.find((n) => n.name === 'Post to CRM');
  assert.deepEqual(crm.credentials, { httpHeaderAuth: { name: 'CRM header auth' } });
});

test('sub-workflow reference by id becomes reference by name', () => {
  const call = mainN.nodes.find((n) => n.name === 'Normalize lead');
  assert.deepEqual(call.parameters.workflowId, { __rl: true, mode: 'id', value: '@workflow:sub: Normalize Lead' });
  assert.equal(mainN.settings.errorWorkflow, '@workflow:sub: Normalize Lead');
  assert.deepEqual(referencedWorkflowNames(mainN).map((r) => r.name), ['sub: Normalize Lead', 'sub: Normalize Lead']);
});

test('unresolved id is kept and warned about', () => {
  const r = normalizeWorkflow(raw[1], { idToName: new Map() });
  assert.equal(r.warnings.length, 1, 'only errorWorkflow is unresolved; the call resolves via cachedResultName');
  assert.match(r.warnings[0], /errorWorkflow.*unresolved/);
});

test('normalization is idempotent and stable', () => {
  for (const w of [subN, mainN]) {
    assert.equal(stringify(normalizeWorkflow(w).workflow), stringify(w));
  }
});

test('nodes sorted by name, keys ordered deterministically', () => {
  assert.deepEqual(mainN.nodes.map((n) => n.name), [...mainN.nodes.map((n) => n.name)].sort());
  assert.deepEqual(Object.keys(mainN).slice(0, 3), ['name', 'tags', 'settings']);
});

test('denormalize restores ids, references, webhookId, tags', () => {
  const nameToId = new Map([['sub: Normalize Lead', 'ExistingSubId0001']]);
  const { workflow, warnings } = denormalizeWorkflow(mainN, { nameToId, knownNames: new Set(['Lead intake', 'sub: Normalize Lead']) });
  assert.deepEqual(warnings, []);
  assert.deepEqual(workflow.nodes.find((n) => n.name === 'Post to CRM').credentials, { httpHeaderAuth: { name: 'CRM header auth' } });
  assert.equal(workflow.settings.errorWorkflow, 'ExistingSubId0001');
  const call = workflow.nodes.find((n) => n.name === 'Normalize lead');
  assert.equal(call.parameters.workflowId.value, 'ExistingSubId0001');
  assert.equal(call.parameters.workflowId.cachedResultName, 'sub: Normalize Lead');
  assert.match(workflow.id, /^[A-Za-z0-9]{16}$/);
  assert.equal(workflow.active, false);
  assert.ok(workflow.nodes.find((n) => n.type.endsWith('.webhook')).webhookId);
  const sub = denormalizeWorkflow(subN).workflow;
  assert.deepEqual(sub.tags, [{ name: 'sub' }]);
});

test('denormalize attaches credentials by name when target has them', () => {
  const credentials = new Map([['httpHeaderAuth|CRM header auth', 'TargetCred00001']]);
  const { workflow } = denormalizeWorkflow(mainN, { credentials });
  assert.equal(workflow.nodes.find((n) => n.name === 'Post to CRM').credentials.httpHeaderAuth.id, 'TargetCred00001');
});

test('normalize(denormalize(x)) == x, even when target ids differ', () => {
  for (const w of [subN, mainN]) {
    const nameToId = new Map([['sub: Normalize Lead', 'Whatever0000000X'], ['Lead intake', 'Another000000000']]);
    const d = denormalizeWorkflow(w, { nameToId, credentials: new Map([['httpHeaderAuth|CRM header auth', 'Zz']]) }).workflow;
    const idToName = buildIdToName([{ id: 'Whatever0000000X', name: 'sub: Normalize Lead' }, { id: 'Another000000000', name: 'Lead intake' }]);
    const back = normalizeWorkflow(d, { idToName }).workflow;
    assert.equal(stringify(back), stringify(w));
  }
});

test('nodeGroups kept only when non-empty', () => {
  assert.ok(!('nodeGroups' in normalizeWorkflow({ name: 'x', nodes: [], nodeGroups: [] }).workflow));
  assert.deepEqual(normalizeWorkflow({ name: 'x', nodes: [], nodeGroups: [{ b: 1, a: 2 }] }).workflow.nodeGroups, [{ a: 2, b: 1 }]);
});

test('missing credential warns only when a credential map is supplied', () => {
  const r = denormalizeWorkflow(mainN, { credentials: new Map() });
  assert.ok(r.warnings.some((w) => /CRM header auth/.test(w)));
});

test('duplicate node names are rejected', () => {
  assert.throws(() => normalizeWorkflow({ name: 'x', nodes: [{ name: 'a', type: 't' }, { name: 'a', type: 't' }] }), /Duplicate node name/);
});
