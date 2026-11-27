# History: the Python-library phase

Everything under this folder describes the earlier phase of the repository, when it was a Python
package (FastAPI module API, `DRY_RUN`/`REPLAY`/`MOCK`/`REAL` execution modes) that n8n called over
HTTP. That phase is closed. The runtime is now n8n workflows only; see
[../architecture.md](../architecture.md).

| Path | What it is |
| --- | --- |
| `releases/` | Release notes 0.1.0 to 0.14.0 of the Python package |
| `project-state/` | Intent, decisions, implementation and verification records, and audits of that phase |
| `modules/` | Per-module guarantees of the Python modules |
| `plan.md`, `module-contract.md`, `module-quality-template.md` | Plan and module contract of that phase |
| `runbooks/` | Operator notes for the old API (`/v1/incidents`) and the Google Forms poller |

These files were moved without edits to their content (only relative links were fixed). Statements
in them such as "198 passed" or "API returns SUCCESS" were true for the Python code at the time and
say nothing about the n8n workflows. The Python code itself is in
[`legacy/python-modules/`](../../legacy/python-modules/); the old lead-management workflows are in
[`examples/lead-management/`](../../examples/lead-management/).
