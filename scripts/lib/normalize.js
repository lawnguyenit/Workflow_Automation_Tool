'use strict';
// Normalize / de-normalize n8n workflow JSON so exports are diff-stable and importable.
const { sortKeys, deterministicUuid, deterministicWorkflowId, tagNames } = require('./common');

const REF_PREFIX = '@workflow:';
// Node types that hold a reference to another workflow in parameters.workflowId.
const WORKFLOW_REF_TYPES = new Set([
  'n8n-nodes-base.executeWorkflow',
  '@n8n/n8n-nodes-langchain.toolWorkflow',
]);
// Node types that n8n gives a persisted webhookId on save (URL path identity). Volatile => derived from node name.
const WEBHOOK_ID_TYPE_RE = /(\.webhook|\.formTrigger|\.chatTrigger|\.wait|\.mcpTrigger|\.telegramTrigger|\.slackTrigger|\.githubTrigger)$/i;

const TOP_ORDER = ['name', 'description', 'tags', 'settings', 'nodes', 'connections', 'nodeGroups'];
const NODE_ORDER = ['id', 'name', 'type', 'typeVersion', 'position', 'parameters', 'credentials'];

function orderKeys(obj, preferred) {
  const out = {};
  for (const k of preferred) if (k in obj) out[k] = obj[k];
  for (const k of Object.keys(obj).sort()) if (!(k in out)) out[k] = obj[k];
  return out;
}

function nodeDeterministicId(name) {
  return deterministicUuid(`node:${name}`);
}

/** Resolve a workflowId-ish value to "@workflow:<name>" form. Returns {value, warning?}. */
function refToName(raw, idToName) {
  if (raw && typeof raw === 'object' && raw.__rl) {
    const v = raw.value;
    if (typeof v === 'string' && v.startsWith(REF_PREFIX)) return { value: { __rl: true, mode: 'id', value: v } };
    if (typeof v === 'string' && v.startsWith('=')) return { value: { __rl: true, mode: raw.mode || 'id', value: v } };
    const name = idToName.get(v) || raw.cachedResultName;
    if (name) return { value: { __rl: true, mode: 'id', value: REF_PREFIX + name } };
    return { value: { __rl: true, mode: 'id', value: v }, warning: `unresolved sub-workflow id ${JSON.stringify(v)}` };
  }
  if (typeof raw === 'string') {
    if (raw.startsWith(REF_PREFIX) || raw.startsWith('=')) return { value: raw };
    const name = idToName.get(raw);
    if (name) return { value: REF_PREFIX + name };
    return { value: raw, warning: `unresolved sub-workflow id ${JSON.stringify(raw)}` };
  }
  return { value: raw };
}

/**
 * Normalize one raw exported workflow.
 * @param {object} wf raw workflow JSON
 * @param {{idToName?: Map<string,string>}} [ctx] id->name for every workflow in the export
 * @returns {{workflow: object, warnings: string[]}}
 */
function normalizeWorkflow(wf, ctx = {}) {
  const idToName = ctx.idToName || new Map();
  const warnings = [];
  const out = {
    name: wf.name,
    tags: [...new Set(tagNames(wf))].sort(),
    settings: JSON.parse(JSON.stringify(wf.settings || {})),
    nodes: [],
    connections: JSON.parse(JSON.stringify(wf.connections || {})),
  };
  if (typeof wf.description === 'string' && wf.description.trim()) out.description = wf.description;
  if (Array.isArray(wf.nodeGroups) && wf.nodeGroups.length) out.nodeGroups = sortKeys(JSON.parse(JSON.stringify(wf.nodeGroups)));

  if (out.settings.errorWorkflow) {
    const r = refToName(out.settings.errorWorkflow, idToName);
    out.settings.errorWorkflow = r.value;
    if (r.warning) warnings.push(`settings.errorWorkflow: ${r.warning}`);
  }

  const seen = new Set();
  for (const n of wf.nodes || []) {
    if (seen.has(n.name)) throw new Error(`Duplicate node name ${JSON.stringify(n.name)} in workflow ${JSON.stringify(wf.name)}`);
    seen.add(n.name);
    const node = JSON.parse(JSON.stringify(n));
    node.id = nodeDeterministicId(node.name);
    delete node.webhookId;
    if (node.credentials) {
      const creds = {};
      for (const [type, c] of Object.entries(node.credentials)) {
        if (!c || !c.name) warnings.push(`node ${node.name}: credential ${type} has no name`);
        creds[type] = c && c.name ? { name: c.name } : {};
      }
      node.credentials = creds;
    }
    if (WORKFLOW_REF_TYPES.has(node.type) && node.parameters && 'workflowId' in node.parameters) {
      const r = refToName(node.parameters.workflowId, idToName);
      node.parameters.workflowId = r.value;
      if (r.warning) warnings.push(`node ${node.name}: ${r.warning}`);
    }
    out.nodes.push(orderKeys(sortKeys(node), NODE_ORDER));
  }
  out.nodes.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  out.connections = sortKeys(out.connections);
  out.settings = sortKeys(out.settings);
  return { workflow: orderKeys(out, TOP_ORDER), warnings };
}

/** Build id->name map from raw workflows. */
function buildIdToName(rawWorkflows) {
  const m = new Map();
  for (const w of rawWorkflows) if (w.id) m.set(String(w.id), w.name);
  return m;
}

/** Normalize a whole export set (resolves cross references between them). */
function normalizeAll(rawWorkflows) {
  const idToName = buildIdToName(rawWorkflows);
  return rawWorkflows.map((w) => normalizeWorkflow(w, { idToName }));
}

function nameFromRef(v) {
  const s = v && typeof v === 'object' ? v.value : v;
  return typeof s === 'string' && s.startsWith(REF_PREFIX) ? s.slice(REF_PREFIX.length) : null;
}

/**
 * Reverse of normalizeWorkflow: produce an importable workflow.
 * @param {object} wf normalized workflow
 * @param {{nameToId?: Map<string,string>, credentials?: Map<string,string>}} ctx
 *   nameToId: ids already used on the target instance (by workflow name); missing names get deterministic ids.
 *   credentials: optional "type|name" -> id map. n8n itself attaches credentials by name+type on import when
 *   the id is absent, so this is only needed to force ids or to warn about missing credentials.
 * @returns {{workflow: object, warnings: string[]}}
 */
function denormalizeWorkflow(wf, ctx = {}) {
  const nameToId = ctx.nameToId || new Map();
  const credentials = ctx.credentials || null;
  const warnings = [];
  const idFor = (name) => nameToId.get(name) || deterministicWorkflowId(name);
  const out = JSON.parse(JSON.stringify(wf));
  out.id = idFor(out.name);
  out.active = false;
  out.tags = (wf.tags || []).map((t) => (typeof t === 'string' ? { name: t } : t));
  if (!out.tags.length) delete out.tags;

  const errName = nameFromRef(out.settings && out.settings.errorWorkflow);
  if (errName) out.settings.errorWorkflow = idFor(errName);

  for (const node of out.nodes) {
    if (WEBHOOK_ID_TYPE_RE.test(node.type)) node.webhookId = deterministicUuid(`webhook:${node.name}`);
    if (node.credentials) {
      for (const [type, c] of Object.entries(node.credentials)) {
        if (!credentials) continue;
        const id = c.name && credentials.get(`${type}|${c.name}`);
        if (id) c.id = id;
        else if (c.name) warnings.push(`node ${node.name}: credential ${type} "${c.name}" not found on target; left unattached`);
      }
    }
    if (WORKFLOW_REF_TYPES.has(node.type) && node.parameters && 'workflowId' in node.parameters) {
      const ref = node.parameters.workflowId;
      const name = nameFromRef(ref);
      if (name) {
        const id = idFor(name);
        node.parameters.workflowId = ref && typeof ref === 'object'
          ? { __rl: true, mode: 'id', value: id, cachedResultName: name }
          : id;
        if (ref && typeof ref === 'object') node.parameters.workflowId.mode = 'id';
        if (!ctx.knownNames || !ctx.knownNames.has(name)) {
          if (ctx.knownNames) warnings.push(`node ${node.name}: sub-workflow "${name}" not present in import set`);
        }
      }
    }
  }
  return { workflow: out, warnings };
}

/** Names of workflows referenced by a normalized workflow (sub-workflow calls and errorWorkflow). */
function referencedWorkflowNames(wf) {
  const names = [];
  const e = nameFromRef(wf.settings && wf.settings.errorWorkflow);
  if (e) names.push({ kind: 'errorWorkflow', name: e, node: null });
  for (const n of wf.nodes || []) {
    if (WORKFLOW_REF_TYPES.has(n.type) && n.parameters && 'workflowId' in n.parameters) {
      const name = nameFromRef(n.parameters.workflowId);
      if (name) names.push({ kind: 'call', name, node: n.name });
    }
  }
  return names;
}

module.exports = {
  normalizeWorkflow, normalizeAll, denormalizeWorkflow, buildIdToName, referencedWorkflowNames,
  nameFromRef, nodeDeterministicId, REF_PREFIX, WORKFLOW_REF_TYPES, WEBHOOK_ID_TYPE_RE,
};
