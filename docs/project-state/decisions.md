# Decisions

| ID | Decision | Status | Evidence |
|---|---|---|---|
| DEC-001 | Define reusable, platform-neutral module contracts; use n8n as the primary reference orchestration runtime and Python/HTTP/API implementations where useful. | approved | User's revised plan, adopted by “cập nhật lại và triển khai đi”. |
| DEC-002 | Build the shared framework and an initial Intelligent Document Intake portfolio workflow before paid client work; keep the broader integration catalog demand-driven. | approved; supersedes the earlier defer-all-implementations decision | User adopted `Downloads/plan_revised.md` and requested implementation. |
| DEC-003 | Use fixtures, mocks, dry-run, and replay for local development without provider credentials; report live-provider and production verification separately. | approved | Revised plan's safe-debug requirements; no Gmail/LLM/client credentials supplied. |
| DEC-004 | Use a tool-scoped advisory researcher for current, source-backed risk checks; workflow lead retains plan decisions and the existing intake/build/audit flow remains. | approved | User previously authorized the role and specified the rest of the flow remains unchanged. |
| DEC-005 | Treat input, processing, storage, and output as pipeline stages; treat reliability/security/infra as cross-cutting concerns unless a concrete module boundary requires a component. | approved | User's original pipeline categories and revised plan's reliability layer. |
| DEC-006 | User-approved reference implementation may be built before a real job, but live external integrations are not claimed verified without credentials and target execution evidence. | approved | Reconciles the revised portfolio scope with workspace data/access and evidence rules. |
