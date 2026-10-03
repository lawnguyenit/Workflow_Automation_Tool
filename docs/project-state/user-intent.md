# User intent

## Confirmed

- The user wants a separate reusable tool for workflow automation modules.
- The catalog should cover input, processing, storage, and output, with infrastructure and operational reliability concerns documented across those stages.
- Most incoming jobs are expected to use n8n; other platforms may be used when a real job requires them.
- Keep the framework contracts platform-neutral, with n8n as the primary reference runtime and Python/HTTP/API execution where appropriate.
- Build an initial production-style portfolio workflow before a paid job: Intelligent Document Intake using Gmail/email ingestion, document extraction, structured LLM extraction, validation, PostgreSQL persistence, and notification.
- Use fixtures/mock/dry-run/replay modes to work safely without provider credentials, while marking live integration verification separately.
- Carefully surface concerns such as duplicate processing, concurrent database writes, overload, retry behavior, and debugging/operability.
- Leave enough bounded, sanitized diagnostic information to debug workflows when the user has no direct system access or only receives workflow nodes/configuration from the client.
- Describe modules in Git updates so maintainers can see their contents, guarantees, and common user concerns.
- For any real job that requires a capability not yet in the reusable catalog, complete the client work and then create a generalized reusable module in the appropriate tool project before closing the job; do not leave the capability as a client-only one-off.
- A shared read-only research agent at `../.codex/agents/workflow-module-risk-researcher.toml` checks bounded concerns against current online sources and reports gaps; the established workflow remains unchanged.

## Assumptions to revisit

- The tool directory name is `Workflow_Automation_Tool`.
- No live Gmail/OpenAI/LLM/PostgreSQL or client environment credentials were supplied; local implementation should support representative fixtures and document live-provider gaps.
- The portfolio is a synthetic reference system, not a real client deployment or production verification.
