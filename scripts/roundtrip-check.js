#!/usr/bin/env node
'use strict';
// Empirical check on a REAL n8n (N8N_BIN, fresh sqlite home unless N8N_USER_FOLDER is set):
//   1. import <dir>                       -> workflow count must match
//   2. export twice, no edits             -> the two normalized exports must be identical
//   3. export == <dir> (import->export->normalize round trip)
//   4. import the export again, export     -> still identical (re-import is idempotent)
//   node scripts/roundtrip-check.js [--dir workflows]
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { findWorkflowFiles, parseArgs } = require('./lib/common');
const { N8nCli } = require('./lib/n8n-cli');
const { importFrom } = require('./n8n-import');
const { exportTo } = require('./n8n-export');

function snapshot(dir) {
  const m = new Map();
  for (const f of findWorkflowFiles(dir)) m.set(path.relative(dir, f).split(path.sep).join('/'), fs.readFileSync(f, 'utf8'));
  return m;
}

function compare(label, a, b, aName, bName) {
  const problems = [];
  for (const k of new Set([...a.keys(), ...b.keys()])) {
    if (!a.has(k)) problems.push(`${label}: ${k} only in ${bName}`);
    else if (!b.has(k)) problems.push(`${label}: ${k} only in ${aName}`);
    else if (a.get(k) !== b.get(k)) {
      const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-diff-'));
      fs.writeFileSync(path.join(tmp, 'a'), a.get(k));
      fs.writeFileSync(path.join(tmp, 'b'), b.get(k));
      const d = spawnSync('diff', ['-u', '--label', `${aName}/${k}`, '--label', `${bName}/${k}`, path.join(tmp, 'a'), path.join(tmp, 'b')], { encoding: 'utf8' });
      fs.rmSync(tmp, { recursive: true, force: true });
      problems.push(`${label}: ${k} differs\n${(d.stdout || '').split('\n').slice(0, 40).join('\n')}`);
    }
  }
  return problems;
}

function main(argv) {
  const { opts } = parseArgs(argv, ['dir']);
  const dir = opts.dir || 'workflows';
  const ownHome = !process.env.N8N_USER_FOLDER;
  const home = ownHome ? fs.mkdtempSync(path.join(os.tmpdir(), 'n8n-rt-home-')) : process.env.N8N_USER_FOLDER;
  process.env.N8N_USER_FOLDER = home;
  const cli = new N8nCli();
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'n8n-rt-'));
  const problems = [];
  try {
    console.log(`n8n ${cli.n8n(['--version']).trim()} at ${cli.describe()}`);
    const input = snapshot(dir);
    const imp = importFrom(cli, dir);
    console.log(`1. import: expected ${imp.expected}, found ${imp.found}`);
    if (imp.missing.length) problems.push(`import missing: ${imp.missing.join(', ')}`);
    for (const w of imp.warnings) console.warn(`   warn ${w}`);

    const A = path.join(work, 'A'); const B = path.join(work, 'B'); const C = path.join(work, 'C');
    for (const d of [A, B, C]) fs.mkdirSync(d);
    exportTo(cli, A); exportTo(cli, B);
    problems.push(...compare('2. export twice', snapshot(A), snapshot(B), 'A', 'B'));
    console.log(`2. export twice: ${snapshot(A).size} file(s) compared`);
    problems.push(...compare('3. round trip', input, snapshot(A), 'input', 'export'));
    console.log(`3. round trip vs input: ${input.size} file(s) compared`);

    const again = importFrom(cli, A);
    if (again.missing.length) problems.push(`re-import missing: ${again.missing.join(', ')}`);
    if (again.totalOnTarget !== imp.totalOnTarget) problems.push(`re-import changed workflow count ${imp.totalOnTarget} -> ${again.totalOnTarget}`);
    exportTo(cli, C);
    problems.push(...compare('4. re-import', snapshot(A), snapshot(C), 'A', 'C'));
    console.log(`4. re-import: count ${imp.totalOnTarget} -> ${again.totalOnTarget}`);
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
    if (ownHome) fs.rmSync(home, { recursive: true, force: true });
  }
  for (const p of problems) console.error(p);
  console.log(problems.length ? `FAILED: ${problems.length} problem(s)` : 'round trip OK');
  return problems.length ? 1 : 0;
}

if (require.main === module) {
  try { process.exit(main(process.argv.slice(2))); } catch (e) { console.error(e.message); process.exit(1); }
}
module.exports = { main };
