#!/usr/bin/env node
'use strict';
// Repository checks: JSON valid, placement, node names, sub-workflow inputs, references, secret-looking values.
//   node scripts/n8n-lint.js [--dir workflows]
// Allowlist (exact strings) for known-safe findings: scripts/lint-allow.json  {"values": ["..."]}
const fs = require('node:fs');
const path = require('node:path');
const { findWorkflowFiles, readJson, parseArgs } = require('./lib/common');
const { lintWorkflows } = require('./lib/lint');

function main(argv) {
  const { opts } = parseArgs(argv, ['dir']);
  const dir = opts.dir || 'workflows';
  const files = findWorkflowFiles(dir);
  const problems = [];
  const items = [];
  for (const f of files) {
    try {
      items.push({ rel: path.relative(dir, path.dirname(f)).split(path.sep).join('/'), wf: readJson(f) });
    } catch (e) {
      problems.push({ file: f, check: 'json', message: e.message });
    }
  }
  const allowFile = path.join(__dirname, 'lint-allow.json');
  const allow = new Set(fs.existsSync(allowFile) ? readJson(allowFile).values || [] : []);
  problems.push(...lintWorkflows(items, { allow }));
  for (const p of problems) console.error(`[${p.check}] ${p.file}: ${p.message}`);
  console.log(`lint: ${files.length} workflow file(s), ${problems.length} problem(s)`);
  return problems.length ? 1 : 0;
}

if (require.main === module) process.exit(main(process.argv.slice(2)));
module.exports = { main };
