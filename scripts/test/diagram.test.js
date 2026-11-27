'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { readJson } = require('../lib/common');
const { normalizeAll } = require('../lib/normalize');
const { toMermaid } = require('../lib/diagram');
const { generateBlock, mergeReadme, BEGIN, END } = require('../lib/readme');

const raw = ['raw-sub.json', 'raw-main.json'].map((f) => readJson(path.join(__dirname, 'fixtures', f)));
const [subN, mainN] = normalizeAll(raw).map((r) => r.workflow);
const ctx = (wf, relDir) => ({
  relDir,
  relDirByName: new Map([[subN.name, 'sub/normalize-lead'], [mainN.name, 'lead-intake']]),
  calledBy: new Map([[subN.name, [mainN.name]]]),
});

test('mermaid: shapes, branch labels, sub-workflow link', () => {
  const m = toMermaid(mainN);
  assert.match(m, /^flowchart LR/);
  assert.match(m, /\["Has email\?"\]|\{"Has email\?"\}/);
  assert.match(m, /\{"Has email\?"\}/);
  assert.match(m, /\(\["Receive lead webhook"\]\):::isTrigger/);
  assert.match(m, /\[\["Normalize lead"\]\]:::isCall/);
  assert.match(m, /-->\|true\|/);
  assert.match(m, /-\.->\|calls\| w0/);
  assert.match(m, /w0\[\["sub: Normalize Lead"\]\]:::isExt/);
  assert.match(m, /err0\(\["on error"\]\):::isExt -\.-> w0/);
  assert.ok(!m.includes('Note'), 'sticky notes are not drawn');
});

test('mermaid: output is deterministic', () => {
  assert.equal(toMermaid(mainN), toMermaid(JSON.parse(JSON.stringify(mainN))));
});

test('mermaid: quotes in names are escaped', () => {
  const wf = { name: 'x', nodes: [{ name: 'say "hi"', type: 'n8n-nodes-base.set', parameters: {} }], connections: {}, settings: {} };
  assert.match(toMermaid(wf), /say #quot;hi#quot;/);
});

test('readme: sections for main workflow', () => {
  const b = generateBlock(mainN, ctx(mainN, 'lead-intake'));
  assert.match(b, /## Purpose\n\n> Receives leads/);
  assert.match(b, /POST \/lead-intake/);
  assert.match(b, /Error workflow: \[sub: Normalize Lead\]\(\.\.\/sub\/normalize-lead\/README\.md\)/);
  assert.match(b, /`Post to CRM` \(continueErrorOutput\)/);
  assert.match(b, /\| Post to CRM \| httpRequest \| CRM header auth \|/);
  assert.ok(b.startsWith(BEGIN) && b.endsWith(END));
});

test('readme: sub-workflow inputs, description, called-by', () => {
  const b = generateBlock(subN, ctx(subN, 'sub/normalize-lead'));
  assert.match(b, /Cleans one lead record/);
  assert.match(b, /\| email \| string \|/);
  assert.match(b, /Called by:\n\n- \[Lead intake\]\(\.\.\/\.\.\/lead-intake\/README\.md\)/);
});

test('readme: manual text outside markers survives regeneration', () => {
  const first = mergeReadme(null, mainN, generateBlock(mainN, ctx(mainN, 'lead-intake')));
  const edited = first.replace('# Lead intake', '# Lead intake\n\nHand-written intro.').replace('## Notes', '## Notes\n\nKeep me.');
  const changed = { ...mainN, description: 'A new description.' };
  const second = mergeReadme(edited, changed, generateBlock(changed, ctx(changed, 'lead-intake')));
  assert.match(second, /Hand-written intro\./);
  assert.match(second, /Keep me\./);
  assert.match(second, /A new description\./);
  assert.equal(second.split(BEGIN).length, 2);
  // idempotent
  assert.equal(mergeReadme(second, changed, generateBlock(changed, ctx(changed, 'lead-intake'))), second);
});

test('readme: file without markers gets the block appended', () => {
  const out = mergeReadme('# Mine\n\ntext\n', mainN, generateBlock(mainN, ctx(mainN, 'lead-intake')));
  assert.ok(out.startsWith('# Mine\n\ntext\n'));
  assert.ok(out.includes(BEGIN));
});
