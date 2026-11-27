'use strict';
// Thin wrapper around the official n8n CLI, either local (N8N_BIN / n8n on PATH, N8N_USER_FOLDER) or inside a docker container.
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { encoding: 'utf8', maxBuffer: 256 * 1024 * 1024, ...opts });
  if (r.error) throw new Error(`Failed to run ${cmd}: ${r.error.message}`);
  return r;
}

class N8nCli {
  /** @param {{docker?: string}} opts docker = container name/id; otherwise local binary. */
  constructor({ docker } = {}) {
    this.docker = docker || null;
    this.bin = process.env.N8N_BIN || 'n8n';
  }

  describe() {
    return this.docker ? `docker container ${this.docker}` : `local ${this.bin} (N8N_USER_FOLDER=${process.env.N8N_USER_FOLDER || '<default ~/.n8n>'})`;
  }

  /** Run `n8n <args>`; throws on non-zero exit. Returns stdout. */
  n8n(args) {
    const r = this.docker
      ? run('docker', ['exec', this.docker, 'n8n', ...args])
      : run(this.bin, args);
    if (r.status !== 0) {
      throw new Error(`n8n ${args.join(' ')} failed (exit ${r.status})\n${r.stdout}\n${r.stderr}`);
    }
    return r.stdout;
  }

  /** Export all workflows (official CLI: --backup => --all --pretty --separate) into a fresh local temp dir. */
  exportAllToLocalDir() {
    const local = fs.mkdtempSync(path.join(os.tmpdir(), 'n8n-export-'));
    if (!this.docker) {
      this.n8n(['export:workflow', '--backup', `--output=${local}`]);
      return local;
    }
    const remote = `/tmp/wf-export-${process.pid}-${Date.now()}`;
    run('docker', ['exec', this.docker, 'mkdir', '-p', remote]);
    try {
      this.n8n(['export:workflow', '--backup', `--output=${remote}`]);
      const r = run('docker', ['cp', `${this.docker}:${remote}/.`, local]);
      if (r.status !== 0) throw new Error(`docker cp failed: ${r.stderr}`);
    } finally {
      run('docker', ['exec', this.docker, 'rm', '-rf', remote]);
    }
    return local;
  }

  /** Import a local dir of <x>.json workflow files via `import:workflow --separate`. */
  importLocalDir(localDir) {
    if (!this.docker) return this.n8n(['import:workflow', '--separate', `--input=${localDir}`]);
    const remote = `/tmp/wf-import-${process.pid}-${Date.now()}`;
    run('docker', ['exec', this.docker, 'mkdir', '-p', remote]);
    try {
      const r = run('docker', ['cp', `${localDir}/.`, `${this.docker}:${remote}`]);
      if (r.status !== 0) throw new Error(`docker cp failed: ${r.stderr}`);
      return this.n8n(['import:workflow', '--separate', `--input=${remote}`]);
    } finally {
      run('docker', ['exec', this.docker, 'rm', '-rf', remote]);
    }
  }

  /** `list:workflow` => [{id, name}] (CLI prints "id|name" per line). */
  listWorkflows() {
    const out = this.n8n(['list:workflow']);
    const rows = [];
    for (const line of out.split('\n')) {
      const m = /^([A-Za-z0-9_-]+)\|(.*)$/.exec(line.trim());
      if (m) rows.push({ id: m[1], name: m[2] });
    }
    return rows;
  }
}

module.exports = { N8nCli };
