#!/usr/bin/env node
'use strict';
// Import workflows/**/workflow.json into n8n (de-normalizes first), then verifies every workflow landed.
//   Local:   N8N_USER_FOLDER=... node scripts/n8n-import.js [--dir workflows]
//   Docker:  node scripts/n8n-import.js --docker <container> [--dir workflows]
// Existing workflows are matched by NAME and keep their id (updated in place); new ones get a deterministic id.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { findWorkflowFiles, readJson, stringify, parseArgs, slugify } = require('./lib/common');
const { denormalizeWorkflow } = require('./lib/normalize');
const { N8nCli } = require('./lib/n8n-cli');

/** Re-export the imported workflows and list credential slots that ended up without an id. */
function unattachedCredentials(cli, expectedIds) {
  const tmp = cli.exportAllToLocalDir();
  const out = [];
  try {
    for (const f of fs.readdirSync(tmp).filter((x) => x.endsWith('.json'))) {
      const w = readJson(path.join(tmp, f));
      if (!expectedIds.has(w.id)) continue;
      for (const n of w.nodes || []) {
        for (const [type, c] of Object.entries(n.credentials || {})) {
          if (!c.id) out.push(`${w.name}: node ${n.name}: credential ${type} "${c.name}" does not exist on the target; create it in n8n, then re-import`);
        }
      }
    }
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  return out;
}

function importFrom(cli, dir) {
  const files = findWorkflowFiles(dir);
  if (!files.length) throw new Error(`No workflow.json found under ${dir}`);
  const wfs = files.map((f) => ({ f, wf: readJson(f) }));
  const names = wfs.map((w) => w.wf.name);
  const dup = names.find((n, i) => names.indexOf(n) !== i);
  if (dup) throw new Error(`Duplicate workflow name in repo: ${dup}`);

  const existing = cli.listWorkflows();
  const nameToId = new Map();
  for (const e of existing) {
    if (nameToId.has(e.name)) throw new Error(`Target has two workflows named ${JSON.stringify(e.name)}; resolve before import`);
    nameToId.set(e.name, e.id);
  }
  const ctx = { nameToId, knownNames: new Set(names) };
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'n8n-import-'));
  const warnings = [];
  try {
    const expectedIds = new Map();
    wfs.forEach(({ wf }, i) => {
      const { workflow, warnings: w } = denormalizeWorkflow(wf, ctx);
      warnings.push(...w.map((x) => `${wf.name}: ${x}`));
      expectedIds.set(workflow.id, wf.name);
      fs.writeFileSync(path.join(tmp, `${String(i).padStart(3, '0')}-${slugify(wf.name)}.json`), stringify(workflow));
    });
    const out = cli.importLocalDir(tmp);
    const after = cli.listWorkflows();
    const afterById = new Map(after.map((a) => [a.id, a.name]));
    const missing = [...expectedIds].filter(([id]) => !afterById.has(id)).map(([, n]) => n);
    // n8n attaches credentials by name+type on import; report the ones it could not find.
    warnings.push(...unattachedCredentials(cli, expectedIds));
    return { expected: expectedIds.size, found: expectedIds.size - missing.length, missing, warnings, cliOutput: out, totalOnTarget: after.length };
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

function main(argv) {
  const { opts } = parseArgs(argv, ['dir', 'docker']);
  const cli = new N8nCli({ docker: opts.docker });
  console.log(`Importing into ${cli.describe()}`);
  const r = importFrom(cli, opts.dir || 'workflows');
  for (const w of r.warnings) console.warn(`warn ${w}`);
  console.log(`expected ${r.expected}, found on target ${r.found} (target total ${r.totalOnTarget})`);
  if (r.missing.length) {
    console.error(`IMPORT INCOMPLETE: missing ${r.missing.length}: ${r.missing.join(', ')}`);
    return 1;
  }
  return 0;
}

if (require.main === module) {
  try { process.exit(main(process.argv.slice(2))); } catch (e) { console.error(e.message); process.exit(1); }
}
module.exports = { importFrom };
