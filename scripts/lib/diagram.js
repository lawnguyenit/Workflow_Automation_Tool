'use strict';
// Mermaid flowchart generator for a normalized n8n workflow. Pure function, no deps.
const { nameFromRef, WORKFLOW_REF_TYPES } = require('./normalize');

function shortType(type) {
  return String(type).replace(/^n8n-nodes-base\./, '').replace(/^@n8n\/n8n-nodes-langchain\./, 'ai.');
}

function isTriggerType(type) {
  const t = shortType(type);
  if (/respondToWebhook/i.test(t)) return false;
  return /trigger$/i.test(t) || /^(webhook|cron|start)$/i.test(t);
}

function esc(text) {
  return String(text).replace(/"/g, '#quot;').replace(/[\r\n]+/g, ' ');
}

function outputLabel(node, idx, totalSlots) {
  const t = shortType(node.type);
  if (t === 'if' || t === 'filter') return idx === 0 ? 'true' : idx === 1 ? 'false' : String(idx);
  if (t === 'splitInBatches') return idx === 0 ? 'done' : 'loop';
  if (totalSlots > 1) return `#${idx + 1}`;
  return '';
}

/**
 * @param {object} wf normalized workflow
 * @returns {string} mermaid source (without fences)
 */
function toMermaid(wf) {
  const nodes = (wf.nodes || []).filter((n) => !/stickyNote$/.test(n.type));
  const ids = new Map(nodes.map((n, i) => [n.name, `n${i}`]));
  const lines = ['flowchart LR'];
  for (const n of nodes) {
    const id = ids.get(n.name);
    const label = esc(n.name);
    const t = shortType(n.type);
    if (isTriggerType(n.type)) lines.push(`  ${id}(["${label}"]):::isTrigger`);
    else if (t === 'if' || t === 'switch' || t === 'filter') lines.push(`  ${id}{"${label}"}`);
    else if (WORKFLOW_REF_TYPES.has(n.type)) lines.push(`  ${id}[["${label}"]]:::isCall`);
    else lines.push(`  ${id}["${label}"]`);
  }
  // Connections (main + ai_* edges).
  const byName = new Map(nodes.map((n) => [n.name, n]));
  for (const [src, types] of Object.entries(wf.connections || {})) {
    if (!ids.has(src)) continue;
    for (const [ctype, slots] of Object.entries(types)) {
      slots.forEach((targets, idx) => {
        for (const t of targets || []) {
          if (!ids.has(t.node)) continue;
          if (ctype === 'main') {
            const lab = outputLabel(byName.get(src), idx, slots.length);
            lines.push(lab ? `  ${ids.get(src)} -->|${lab}| ${ids.get(t.node)}` : `  ${ids.get(src)} --> ${ids.get(t.node)}`);
          } else {
            lines.push(`  ${ids.get(src)} -. ${ctype} .-> ${ids.get(t.node)}`);
          }
        }
      });
    }
  }
  // Calls to other workflows, shown as links to external nodes.
  const ext = new Map();
  const extId = (name) => {
    if (!ext.has(name)) ext.set(name, `w${ext.size}`);
    return ext.get(name);
  };
  for (const n of nodes) {
    if (!WORKFLOW_REF_TYPES.has(n.type) || !n.parameters) continue;
    const name = nameFromRef(n.parameters.workflowId);
    if (name) lines.push(`  ${ids.get(n.name)} -.->|calls| ${extId(name)}`);
  }
  const errName = nameFromRef(wf.settings && wf.settings.errorWorkflow);
  if (errName) lines.push(`  err0(["on error"]):::isExt -.-> ${extId(errName)}`);
  for (const [name, id] of ext) lines.push(`  ${id}[["${esc(name)}"]]:::isExt`);
  lines.push('  classDef isTrigger stroke-width:3px');
  lines.push('  classDef isCall stroke-dasharray: 4 3');
  lines.push('  classDef isExt stroke-dasharray: 2 2,fill:#f4f4f4,color:#333');
  return lines.join('\n');
}

module.exports = { toMermaid, shortType, isTriggerType };
