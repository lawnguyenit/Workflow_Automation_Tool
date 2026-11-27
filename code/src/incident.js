'use strict';
// Build an incident_log row from an n8n Error Trigger payload without personal data:
// no email body, subject, sender, attachment content or item data. The error message
// is truncated and e-mail addresses / long digit runs are masked.

const MAX_MESSAGE = 300;

function scrub(text) {
  if (typeof text !== 'string') return null;
  return text
    .replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, '[email]')
    .replace(/\d{6,}/g, '[number]')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, MAX_MESSAGE) || null;
}

function shortText(value, max = 200) {
  return typeof value === 'string' && value.trim() ? value.trim().slice(0, max) : null;
}

function incidentRow(errorPayload) {
  const payload = errorPayload && typeof errorPayload === 'object' ? errorPayload : {};
  const execution = payload.execution && typeof payload.execution === 'object' ? payload.execution : {};
  const workflow = payload.workflow && typeof payload.workflow === 'object' ? payload.workflow : {};
  const trigger = payload.trigger && typeof payload.trigger === 'object' ? payload.trigger : {};
  const error = execution.error || trigger.error || {};
  return {
    workflow_id: shortText(workflow.id, 64),
    workflow_name: shortText(workflow.name),
    execution_id: execution.id === undefined || execution.id === null ? null : String(execution.id).slice(0, 64),
    failed_node: shortText(execution.lastNodeExecuted || (error.node && error.node.name)),
    error_name: shortText(error.name, 100),
    error_message: scrub(error.message),
    execution_mode: shortText(execution.mode, 32),
  };
}

module.exports = { scrub, incidentRow, MAX_MESSAGE };
