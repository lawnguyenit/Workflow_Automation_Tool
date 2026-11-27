#!/usr/bin/env node
'use strict';
// Normalize workflow.json files in place, or verify they are already normalized (--check).
//   node scripts/n8n-normalize.js [--dir workflows] [--check]
const fs = require('node:fs');
const { findWorkflowFiles, readJson, stringify, parseArgs } = require('./lib/common');
const { normalizeWorkflow, buildIdToName } = require('./lib/normalize');

function main(argv) {
  const { opts } = parseArgs(argv, ['dir']);
  const dir = opts.dir || 'workflows';
  const files = findWorkflowFiles(dir);
  const raws = files.map((f) => ({ f, wf: readJson(f) }));
  const idToName = buildIdToName(raws.map((r) => r.wf));
  const bad = [];
  let warned = 0;
  for (const { f, wf } of raws) {
    const { workflow, warnings } = normalizeWorkflow(wf, { idToName });
    for (const w of warnings) { warned++; console.warn(`warn ${f}: ${w}`); }
    const text = stringify(workflow);
    if (fs.readFileSync(f, 'utf8') !== text) {
      bad.push(f);
      if (!opts.check) fs.writeFileSync(f, text);
    }
  }
  if (opts.check) {
    if (bad.length) {
      console.error(`Not in normalized form (${bad.length}/${files.length}):\n  ${bad.join('\n  ')}\nRun: node scripts/n8n-normalize.js --dir ${dir}`);
      return 1;
    }
    console.log(`normalize --check: ${files.length} workflow file(s) stable`);
    return 0;
  }
  console.log(`normalize: ${files.length} file(s), ${bad.length} rewritten, ${warned} warning(s)`);
  return 0;
}

if (require.main === module) process.exit(main(process.argv.slice(2)));
module.exports = { main };
