#!/usr/bin/env node
'use strict';
// Export all workflows from an n8n instance into workflows/<slug>/workflow.json (sub: workflows/sub/<slug>/).
// Uses ONLY `n8n export:workflow --backup` (no credentials are ever exported).
//   Local:   N8N_USER_FOLDER=/path/to/.n8n [N8N_BIN=/path/to/n8n] node scripts/n8n-export.js [--out workflows] [--prune]
//   Docker:  node scripts/n8n-export.js --docker <container> [--out workflows] [--prune]
const fs = require('node:fs');
const path = require('node:path');
const { readJson, stringify, parseArgs, workflowRelDir } = require('./lib/common');
const { normalizeAll } = require('./lib/normalize');
const { N8nCli } = require('./lib/n8n-cli');

function exportTo(cli, outDir, { prune = false } = {}) {
  const tmp = cli.exportAllToLocalDir();
  try {
    const files = fs.readdirSync(tmp).filter((f) => f.endsWith('.json'));
    const raws = files.map((f) => readJson(path.join(tmp, f)));
    const normalized = normalizeAll(raws);
    const targets = new Map();
    const warnings = [];
    for (const { workflow, warnings: w } of normalized) {
      warnings.push(...w.map((x) => `${workflow.name}: ${x}`));
      const rel = workflowRelDir(workflow);
      if (targets.has(rel)) throw new Error(`Two workflows map to ${rel}: ${targets.get(rel).name} and ${workflow.name}`);
      targets.set(rel, workflow);
    }
    for (const [rel, wf] of targets) {
      const dir = path.join(outDir, rel);
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, 'workflow.json'), stringify(wf));
    }
    const removed = [];
    if (prune) {
      for (const f of require('./lib/common').findWorkflowFiles(outDir)) {
        const rel = path.relative(outDir, path.dirname(f)).split(path.sep).join('/');
        if (!targets.has(rel)) { fs.rmSync(path.dirname(f), { recursive: true, force: true }); removed.push(rel); }
      }
    }
    return { count: targets.size, files: files.length, warnings, removed };
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

function main(argv) {
  const { opts } = parseArgs(argv, ['out', 'docker']);
  const cli = new N8nCli({ docker: opts.docker });
  console.log(`Exporting from ${cli.describe()}`);
  const r = exportTo(cli, opts.out || 'workflows', { prune: !!opts.prune });
  for (const w of r.warnings) console.warn(`warn ${w}`);
  for (const d of r.removed) console.log(`pruned ${d}`);
  console.log(`exported ${r.count} workflow(s) (${r.files} file(s) from CLI) to ${opts.out || 'workflows'}`);
  return r.count === r.files ? 0 : 1;
}

if (require.main === module) {
  try { process.exit(main(process.argv.slice(2))); } catch (e) { console.error(e.message); process.exit(1); }
}
module.exports = { exportTo };
