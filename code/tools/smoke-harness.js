#!/usr/bin/env node
'use strict';
// Builds local smoke-test workflows that call the real (committed) sub-workflows with
// synthetic fixtures. Nothing here talks to Google: Gmail nodes become No-Op nodes and the
// Gemini call is never reached because the test seeds ai_call_ledger at the daily cap.
//
//   N8N_TOOLING_DIR=<checkout with scripts/lib> node code/tools/smoke-harness.js <out-dir>
//
// Workflow ids are the deterministic ids that scripts/n8n-import.js gives on a fresh
// instance, so import the real workflows with scripts/n8n-import.js first, then the
// harness files with `n8n import:workflow --separate --input=<out-dir>`, then run
// `n8n execute --id=<id> --rawOutput`. Steps and results: docs/architecture.md.

const fs = require('node:fs');
const path = require('node:path');
const { WF, V } = require('./build-workflows');

const ROOT = path.resolve(__dirname, '..', '..');
const LIB_DIRS = [path.join(ROOT, 'scripts', 'lib'), process.env.N8N_TOOLING_DIR && path.join(process.env.N8N_TOOLING_DIR, 'scripts', 'lib')].filter(Boolean);
const LIB_DIR = LIB_DIRS.find((d) => fs.existsSync(path.join(d, 'normalize.js')));
if (!LIB_DIR) { console.error('scripts/lib not found; set N8N_TOOLING_DIR'); process.exit(2); }
const { denormalizeWorkflow } = require(path.join(LIB_DIR, 'normalize.js'));

const FIX = path.join(ROOT, 'tests', 'fixtures');
const pdf = (name) => fs.readFileSync(path.join(FIX, 'pdf', name)).toString('base64');
const emails = JSON.parse(fs.readFileSync(path.join(FIX, 'email', 'emails.json'), 'utf8')).fixtures;
const { sha256Hex } = require('../src/sha256');
const { duplicateKey } = require('../src/duplicate-key');
const committed = (w) => JSON.parse(fs.readFileSync(path.join(ROOT, 'workflows', w.dir, 'workflow.json'), 'utf8'));
const clone = (x) => JSON.parse(JSON.stringify(x));

let counter = 0;
const nid = () => `00000000-0000-4000-8000-${String(++counter).padStart(12, '0')}`;
const manual = (x = 0) => ({ id: nid(), name: 'Start smoke test', type: 'n8n-nodes-base.manualTrigger', typeVersion: 1, position: [x, 0], parameters: {} });
const code = (name, jsCode, x = 220) => ({ id: nid(), name, type: 'n8n-nodes-base.code', typeVersion: V.code, position: [x, 0], parameters: { jsCode } });
const toFile = (x = 440) => ({
  id: nid(), name: 'Turn base64 into PDF file', type: 'n8n-nodes-base.convertToFile', typeVersion: 1.1, position: [x, 0],
  parameters: { operation: 'toBinary', sourceProperty: 'pdf_base64', binaryPropertyName: 'invoice_pdf', options: { fileName: '={{ $json.filename }}', mimeType: 'application/pdf' } },
});
// Copy the orchestrator's real call node for a sub-workflow, optionally with other input mappings.
function call(name, target, inputs, x) {
  const real = committed(WF.intake).nodes.find((n) => n.type === 'n8n-nodes-base.executeWorkflow' && n.parameters.workflowId.value === `@workflow:${target.name}`);
  const node = clone(real);
  Object.assign(node, { name, id: nid(), position: [x, 0] });
  if (inputs) node.parameters.workflowInputs.value = inputs;
  return node;
}
function linear(name, nodes) {
  const connections = {};
  for (let i = 0; i < nodes.length - 1; i++) connections[nodes[i].name] = { main: [[{ node: nodes[i + 1].name, type: 'main', index: 0 }]] };
  return { name, nodes, connections, settings: { executionOrder: 'v1' } };
}

const invoice = { record_key: 'invoice:smoke', document_type: 'invoice', sender: 'Synthetic Supplier', invoice_number: 'INV-1001', amount: 1200, currency: 'usd', status: 'EXTRACTED', confidence: 0.9, missing_fields: [], conflicts: [] };
const DUP = emails.synthetic_duplicate[0];
const DUP_SHA = sha256Hex(Buffer.from(DUP.attachments[0].content_base64, 'base64'));
// The store harness writes this key, so check-duplicate and the orchestrator copy see it as stored.
const KEY_A = duplicateKey(DUP.message_id, DUP_SHA);

const harnesses = [
  linear('smoke validate invoice', [
    manual(),
    code('Synthetic invoices', `return ${JSON.stringify([
      { invoice }, { invoice: { ...invoice, amount: -5 } }, { invoice: { ...invoice, status: 'REVIEW_REQUIRED', error_code: 'OCR_REQUIRED' } },
    ])}.map((json) => ({ json }));`),
    call('Run validate invoice', WF.validate, { invoice: '={{ $json.invoice }}' }, 440),
  ]),
  linear('smoke store invoice', [
    manual(),
    code('Same key three times', `const inv = ${JSON.stringify({ ...invoice, currency: 'USD' })};
return [inv, inv, { ...inv, amount: 1300 }].map((invoice) => ({ json: { idempotency_key: '${KEY_A}', message_id: '${DUP.message_id}', attachment_id: 'attachment_0', filename: 'invoice-1001.pdf', invoice } }));`),
    call('Run store invoice', WF.store, Object.fromEntries(['idempotency_key', 'message_id', 'attachment_id', 'filename', 'invoice'].map((f) => [f, `={{ $json.${f} }}`])), 440),
  ]),
  linear('smoke check duplicate', [
    manual(),
    code('Fixture duplicate pair and a new one', `return ${JSON.stringify([
      { message_id: DUP.message_id, attachment_sha256: DUP_SHA }, { message_id: 'never-seen-message', attachment_sha256: 'b'.repeat(64) },
    ])}.map((json) => ({ json }));`),
    call('Run check duplicate', WF.checkDuplicate, null, 440),
  ]),
  linear('smoke extract invoice', [
    manual(),
    code('Fixture PDFs', `return ${JSON.stringify(['synthetic_invoice.pdf', 'synthetic_image_only.pdf', 'synthetic_corrupt.pdf'].map((f, i) => ({
      idempotency_key: String(i + 1).repeat(64), message_id: `smoke-extract-${i + 1}`, attachment_id: 'attachment_0', filename: f, pdf_base64: pdf(f),
    })))}.map((json) => ({ json }));`),
    toFile(),
    call('Run extract invoice', WF.extract, Object.fromEntries(['idempotency_key', 'message_id', 'attachment_id', 'filename'].map((f) => [f, `={{ $('Fixture PDFs').item.json.${f} }}`])), 660),
  ]),
];

// Orchestrator copy: Gmail trigger -> fixture emails with binary; notify call -> No-Op.
const intake = clone(committed(WF.intake));
intake.name = 'smoke orchestrator routing';
delete intake.settings.errorWorkflow;
const trig = intake.nodes.find((x) => x.type === 'n8n-nodes-base.gmailTrigger');
const fakeEmails = [emails.synthetic_invoice[0], DUP, emails.synthetic_no_attachment[0]];
Object.assign(trig, {
  type: 'n8n-nodes-base.code', typeVersion: V.code,
  parameters: { jsCode: `// Shaped like Gmail Trigger output (simple: false, downloadAttachments: true).
const emails = ${JSON.stringify(fakeEmails.map((m) => ({ id: m.message_id, attachments: m.attachments.map((a) => ({ fileName: a.filename, mimeType: a.mime_type, data: a.content_base64 })) })))};
const out = [];
for (const e of emails) {
  const binary = {};
  for (const [i, a] of e.attachments.entries()) binary['attachment_' + i] = await this.helpers.prepareBinaryData(Buffer.from(a.data, 'base64'), a.fileName, a.mimeType);
  out.push({ json: { id: e.id }, binary });
}
return out;` },
});
delete trig.credentials;
Object.assign(intake.nodes.find((x) => x.name === 'Call: Send invoice notification'), { type: 'n8n-nodes-base.noOp', typeVersion: 1, parameters: {} });
intake.nodes.find((x) => x.name === 'Final status: notified').parameters.assignments.assignments
  .find((a) => a.name === 'final_status').value = '={{ "WOULD_NOTIFY_" + $json.status }}';
const start = manual(-220);
intake.nodes.push(start);
intake.connections[start.name] = { main: [[{ node: trig.name, type: 'main', index: 0 }]] };
harnesses.push(intake);

// Error handler copy: Error Trigger -> manual + synthetic error payload; Gmail -> No-Op.
const errs = clone(committed(WF.errors));
errs.name = 'smoke error handler';
const et = errs.nodes.find((x) => x.type === 'n8n-nodes-base.errorTrigger');
Object.assign(et, { type: 'n8n-nodes-base.code', typeVersion: V.code, parameters: { jsCode: `return [{ json: ${JSON.stringify({
  execution: { id: '991', mode: 'trigger', lastNodeExecuted: 'Insert invoice if key is new', error: { name: 'NodeOperationError', message: 'duplicate for alice@example.invalid account 123456789 failed' } },
  workflow: { id: 'smokeWorkflowId1', name: WF.store.name },
})} }];` } });
const mail = errs.nodes.find((x) => x.type === 'n8n-nodes-base.gmail');
Object.assign(mail, { type: 'n8n-nodes-base.noOp', typeVersion: 1, parameters: {} });
delete mail.credentials;
const start2 = manual(-220);
errs.nodes.push(start2);
errs.connections[start2.name] = { main: [[{ node: et.name, type: 'main', index: 0 }]] };
harnesses.push(errs);

if (require.main === module) {
  const out = process.argv[2];
  if (!out) { console.error('usage: smoke-harness.js <out-dir>'); process.exit(2); }
  fs.mkdirSync(out, { recursive: true });
  for (const h of harnesses) {
    const { workflow } = denormalizeWorkflow(h);
    fs.writeFileSync(path.join(out, `${workflow.id}.json`), `${JSON.stringify(workflow, null, 2)}\n`);
    console.log(`${workflow.id}  ${workflow.name}`);
  }
}

module.exports = { harnesses };
