# n8n-as-code tooling

Workflows live in git as normalized JSON; n8n is the runtime. Scripts in `scripts/` (Node.js, no npm dependencies) move workflows between the two and keep diffs reviewable.

## Requirements

| What | Version | Why |
| --- | --- | --- |
| n8n | **2.49.8** (pinned in `infra/docker-compose.yml`) | one version everywhere; bump deliberately |
| Node.js for `scripts/*` itself | 22+ | no dependencies |
| Node.js for a *local* `n8n` binary | **24+** | n8n 2.49.8 declares `engines.node >=24`; the CLI also needs the `isolated-vm` native module built for that Node |
| Docker | any recent Compose v2 | for the stack and for `--docker` mode |

## Layout

```
workflows/<slug>/workflow.json        main workflows
workflows/sub/<slug>/workflow.json    sub-workflows (name starts with "sub: " or tag "sub")
workflows/**/README.md                generated: diagram + purpose/trigger/inputs/outputs/errors/nodes
```

`<slug>` = workflow name, lowercase ASCII (`sub: ` prefix removed for sub-workflows). Two workflows must not map to the same slug.

## Run the stack

```bash
cp .env.example .env                  # set POSTGRES_PASSWORD and N8N_ENCRYPTION_KEY
docker compose up -d                  # root docker-compose.yml just `include`s infra/docker-compose.yml
# equivalent: docker compose --env-file .env -f infra/docker-compose.yml up -d
```

n8n: http://127.0.0.1:5678 (bound to localhost only). Postgres is not published. Back up `N8N_ENCRYPTION_KEY`: without it stored credentials are unreadable.

## Commands

| Script | Purpose |
| --- | --- |
| `n8n-export.js` | n8n -> repo. Runs `n8n export:workflow --backup`, normalizes, writes `workflows/<slug>/workflow.json`. `--out DIR`, `--prune` (delete folders of workflows no longer in n8n, README included), `--docker CONTAINER` |
| `n8n-normalize.js` | Normalize files in place; `--check` fails if any file is not already normalized |
| `n8n-import.js` | repo -> n8n. De-normalizes, runs `n8n import:workflow --separate`, then re-lists and **fails if any workflow is missing**; also warns about credentials that do not exist on the target |
| `n8n-docs.js` | Generate Mermaid diagram + README per workflow; `--check` fails if stale |
| `n8n-lint.js` | Repository checks (see CI) |
| `roundtrip-check.js` | Empirical import/export round trip on a real n8n (fresh SQLite home unless `N8N_USER_FOLDER` is set) |

Local n8n (needs Node 24+; `N8N_USER_FOLDER` selects the instance, default `~/.n8n`):

```bash
N8N_USER_FOLDER=~/.n8n node scripts/n8n-export.js          # uses `n8n` from PATH, or N8N_BIN=/path/to/n8n
N8N_USER_FOLDER=~/.n8n node scripts/n8n-import.js
```

Inside the compose stack (no local n8n needed; the scripts run on the host and use `docker exec` / `docker cp`):

```bash
node scripts/n8n-export.js --docker "$(docker compose ps -q n8n)"
node scripts/n8n-import.js --docker "$(docker compose ps -q n8n)"
```

Normal loop: edit in the n8n UI -> `n8n-export.js` -> `n8n-docs.js` -> review the diff -> commit. Or edit JSON -> `n8n-normalize.js` -> `n8n-import.js` to try it.

## What normalization does

Whitelist of top-level keys kept: `name`, `description`, `tags` (names only, sorted), `settings`, `nodes`, `connections`, `nodeGroups` (only if non-empty). Everything else is dropped (`id`, `versionId`, `activeVersionId`, `versionCounter`, `meta`, `pinData`, `staticData`, `active`, `isArchived`, `triggerCount`, `shared`, `createdAt`/`updatedAt`, ...).

- Node `id` -> UUID derived from the node name (so **renaming a node changes its id**; that is expected). `webhookId` is dropped and re-derived from the node name on import, which keeps webhook URLs stable across environments.
- Credentials: `{ "type": { "id", "name" } }` -> `{ "type": { "name" } }`. On import n8n itself matches credentials by name+type; missing ones stay unattached and are reported.
- Sub-workflow references (`Execute Workflow` / `Call n8n Workflow Tool` `workflowId`, and `settings.errorWorkflow`) id -> `@workflow:<workflow name>`. On import the id of an existing workflow with that name is reused, otherwise a deterministic id is generated. **Workflow names must therefore be unique.**
- Keys sorted recursively, `nodes` sorted by name; `connections` arrays keep their order (it is meaningful).
- Output: 2-space JSON, trailing newline.

Caveat: sorting `nodes` by name and rewriting canvas-independent fields means the file is not byte-identical to what n8n emits; only the normalized form is compared.

## CI (`.github/workflows/n8n-checks.yml`)

`static` job (Node 22): unit tests; `n8n-lint.js` (JSON valid, folder matches name, no default node names such as `HTTP Request1`/`Code`/`IF`, every `sub:` workflow declares inputs via workflow-inputs or a JSON example (passthrough fails), referenced workflows exist, no secret-looking values: API keys, bearer/JWT tokens, private keys, non-example emails, phone numbers outside the fictional 555-01xx range); normalization stable; diagrams/READMEs match; Compose valid, localhost-only ports, no `latest`/`stable` tag, no `.env` committed.

`roundtrip` job (Node 24): installs the n8n version pinned in the compose file and runs `roundtrip-check.js`.

Known-safe findings can be listed (exact strings) in `scripts/lint-allow.json` as `{"values": ["..."]}`.

## Generated READMEs

Only the text between `<!-- n8n-docs:begin ... -->` and `<!-- n8n-docs:end -->` is rewritten; everything else survives. Purpose comes from the workflow `description`, else sticky-note text. Diagrams are Mermaid rendered by GitHub; sub-workflow calls and the error workflow are drawn as dashed links to external nodes (Mermaid `click` links are not used because GitHub disables them).

## Unit tests

`node --test "scripts/test/*.test.js"` (Node 22) - normalize/de-normalize round trip, diagram and README generation, lint rules.
