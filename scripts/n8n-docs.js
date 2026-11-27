#!/usr/bin/env node
'use strict';
// Generate Mermaid diagram + README.md next to every workflow.json. Manual text outside the markers survives.
//   node scripts/n8n-docs.js [--dir workflows] [--check]
const fs = require('node:fs');
const path = require('node:path');
const { findWorkflowFiles, readJson, parseArgs } = require('./lib/common');
const { generateBlock, mergeReadme } = require('./lib/readme');
const { referencedWorkflowNames } = require('./lib/normalize');

function main(argv) {
  const { opts } = parseArgs(argv, ['dir']);
  const dir = opts.dir || 'workflows';
  const files = findWorkflowFiles(dir);
  const items = files.map((f) => ({ f, wf: readJson(f), relDir: path.relative(dir, path.dirname(f)).split(path.sep).join('/') }));
  const relDirByName = new Map(items.map((i) => [i.wf.name, i.relDir]));
  const calledBy = new Map();
  for (const i of items) {
    for (const r of referencedWorkflowNames(i.wf)) {
      if (r.kind !== 'call') continue;
      if (!calledBy.has(r.name)) calledBy.set(r.name, []);
      if (!calledBy.get(r.name).includes(i.wf.name)) calledBy.get(r.name).push(i.wf.name);
    }
  }
  const stale = [];
  for (const i of items) {
    const readme = path.join(path.dirname(i.f), 'README.md');
    const existing = fs.existsSync(readme) ? fs.readFileSync(readme, 'utf8') : null;
    const block = generateBlock(i.wf, { relDir: i.relDir, relDirByName, calledBy });
    const next = mergeReadme(existing, i.wf, block);
    if (next !== existing) {
      stale.push(readme);
      if (!opts.check) fs.writeFileSync(readme, next);
    }
  }
  if (opts.check) {
    if (stale.length) { console.error(`Docs out of date (${stale.length}/${items.length}):\n  ${stale.join('\n  ')}\nRun: node scripts/n8n-docs.js --dir ${dir}`); return 1; }
    console.log(`docs --check: ${items.length} README(s) up to date`);
    return 0;
  }
  console.log(`docs: ${items.length} workflow(s), ${stale.length} README(s) written`);
  return 0;
}

if (require.main === module) {
  try { process.exit(main(process.argv.slice(2))); } catch (e) { console.error(e.message); process.exit(1); }
}
module.exports = { main };
