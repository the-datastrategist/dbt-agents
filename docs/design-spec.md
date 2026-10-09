# dbt-agents design specification

| Field | Value |
|---|---|
| Status | Accepted; v1 contract frozen by ADR-0001 |
| Version | 1.0 |
| Date | 2026-10-07 |
| Initial consumer | Keylo dbt project |
| Initial warehouse | BigQuery |
| Primary interfaces | MCP and CLI |

## 1. Purpose

Build a lightweight, open-source toolkit that lets an AI coding agent safely:

1. inspect a local dbt repository and its dependency graph;
2. review dbt SQL, YAML, macros, tests, and surrounding application code;
3. inspect warehouse metadata and execute bounded, read-only diagnostic SQL;
4. parse, compile, test, run, and build selected dbt resources;
5. propose, apply, validate, and optionally publish code fixes; and
6. support ChatGPT, Codex, local terminals, and GitHub Actions without coupling
   the core to one model vendor or warehouse.

The initial target is:

- dbt repository: `/Users/gordonsilvera/Repos/theDataStrategist/keylo/keylo-dbt`
- broader codebase: `/Users/gordonsilvera/Repos/theDataStrategist/keylo`
- GCP project: `verdant-abacus-481415-d2`
- dbt Core 1.11 with `dbt-bigquery`

These values are example configuration, never hard-coded runtime assumptions.

## 2. Design recommendation

Start with **one conversational agent and a deterministic tool server**, not a
network of autonomous agents. The model already performs planning and review;
the tool server should own credentials, policy enforcement, command execution,
and auditable results. This is easier to secure, test, package, and explain.

Specialized reviewer, investigator, and fixer agents may be added later as
prompt/skill profiles over the same tool contracts. They must not receive
additional privileges merely because they are separate agents.

Use a Python package that exposes two thin interfaces over one core:

- a local MCP server for Codex;
- a streamable HTTP MCP endpoint, reached through a secure tunnel, for ChatGPT
  web during local development; and
- a CLI for humans and deterministic GitHub Actions jobs.

Official OpenAI documentation states that custom MCP servers can expose read
and write tools to ChatGPT, that ChatGPT can connect by server URL or secure
tunnel, and that write actions require confirmation by default. Tool
annotations remain advisory; authorization and validation must be enforced by
this server. See [Add custom MCP server](https://developers.openai.com/api/docs/guides/custom-mcp-server)
and [Build an MCP server](https://developers.openai.com/plugins/build/mcp-server).

## 3. Goals and non-goals

### 3.1 Goals

| ID | Goal |
|---|---|
| G-01 | Review repository and dbt code using local files and dbt artifacts. |
| G-02 | Execute warehouse diagnostics without allowing ad-hoc data mutation. |
| G-03 | Permit mutations to allowlisted dbt-owned datasets only through an invoked dbt command. |
| G-04 | Apply code changes locally, validate them, and optionally create branches, commits, and pull requests with user approval. |
| G-05 | Switch projects, datasets, profiles, repositories, and warehouses through configuration. |
| G-06 | Run locally with application-default credentials or service-account impersonation, and in CI with workload identity. |
| G-07 | Produce structured, inspectable evidence for every review, diagnosis, and change. |

### 3.2 Non-goals for v1

- A hosted multi-tenant control plane.
- A custom chat UI.
- Unattended production deployment.
- Arbitrary shell access exposed as an MCP tool.
- General-purpose warehouse administration.
- Direct `INSERT`, `UPDATE`, `DELETE`, `MERGE`, DDL, load, copy, or export tools.
- Silent pushes, pull requests, merges, or production dbt runs.
- A vector database. Repository search, dbt artifacts, and bounded file reads
  are sufficient initially.

## 4. System context

```text
 ChatGPT web              Codex                 Terminal / GitHub Actions
      |                     |                              |
 secure tunnel + HTTP       | stdio or HTTP                | CLI
      +---------------------+------------------------------+
                            |
                    dbt-agents interfaces
                     (MCP server + CLI)
                            |
        +-------------------+--------------------+
        |                   |                    |
   Policy engine       Workflow service     Audit/artifacts
        |                   |
  +-----+------+-------+----+----------+
  |            |       |               |
Repo/Git    dbt Core  Warehouse       Process runner
adapter     adapter   provider SPI    (allowlisted commands)
                         |
                   BigQuery provider
                         |
        +----------------+----------------+
        |                                 |
 read-only query identity        dbt-runner identity
 source + target read            source read + dbt-target write
```

The LLM never receives a credential and never connects to BigQuery directly.
It invokes narrow tools. The server validates arguments, selects the correct
identity, executes the operation, limits the result, and records an audit event.

## 5. Trust and permission model

### 5.1 Two execution identities

The requirement “do not change data except through dbt” cannot be strongly
enforced if arbitrary SQL and dbt share one broadly privileged credential.
Use two principals:

| Capability | Query identity | dbt-runner identity |
|---|---:|---:|
| Create BigQuery jobs | Yes | Yes |
| Read allowlisted source datasets | Yes | Yes |
| Read dbt-owned datasets | Yes | Yes |
| Write source datasets | No | No |
| Write dbt-owned development/CI datasets | No | Yes |
| Write production dbt datasets | No | Optional, separate production runner only |
| IAM, billing, project administration | No | No |

For BigQuery, the intended shape is:

- project-level `roles/bigquery.jobUser` for each runtime principal;
- dataset-level `roles/bigquery.dataViewer` on allowlisted sources;
- dataset-level `roles/bigquery.dataViewer` on dbt targets for the query identity;
- dataset-level `roles/bigquery.dataEditor` on explicit dbt-owned targets for
  the dbt runner; and
- no project-wide `dataEditor`, `dataOwner`, or admin roles.

Exact IAM bindings are deployment-specific and must be verified by an automated
boundary test. The existing Keylo project already follows this source-reader /
dbt-target-writer pattern and rejects a target dataset equal to its source.

### 5.2 Authentication modes

| Mode | Use | Assurance |
|---|---|---|
| `adc` | Fast local setup using `gcloud auth application-default login` | Convenient, but actual access may exceed configured policy; the server still enforces policy. |
| `impersonation` | Recommended local mode; ADC impersonates separate query and dbt service accounts | Strong separation without downloaded keys. |
| `service_account_file` | Compatibility option via an external path/environment variable | Supported but discouraged; never copy or commit keys. |
| `workload_identity` | GitHub Actions and hosted deployments | Recommended CI mode; short-lived credentials and no stored key. |

Authentication selects credentials; authorization is still decided by the
policy engine and IAM. Configuration must never contain credential contents.

### 5.3 Approval levels

| Level | Examples | Default behavior |
|---|---|---|
| A0: inspect | Read files, search, `git diff`, list metadata, dbt parse artifacts | Run without confirmation. |
| A1: bounded compute | BigQuery dry run/read query, `dbt compile` | Run without confirmation within cost and row limits; otherwise reject. |
| A2: reversible local change | Edit allowlisted files, create branch, run dev/CI dbt models or tests | Require confirmation of the proposed plan and targets. |
| A3: remote/consequential | Push, open PR, production dbt invocation | Explicit confirmation for each operation; production may also require environment approval. |
| Forbidden | Direct DML/DDL, source writes, IAM changes, merge PR, force push | Tool does not expose or execute it. |

MCP annotations must accurately mark read-only and destructive behavior, but
server-side policy is authoritative.

## 6. Functional requirements

### 6.1 Project and repository

| ID | Requirement |
|---|---|
| FR-100 | List configured dbt-agents project aliases without credentials or external discovery, and distinguish each alias from its warehouse project identifier. |
| FR-101 | Discover dbt projects only below configured repository roots. |
| FR-102 | Read/search text files while honoring ignore rules, file-size limits, binary detection, and denylisted paths. |
| FR-103 | Inspect Git branch, status, tracked diff, and recent relevant history. |
| FR-104 | Refuse edits outside configured writable roots or to secrets, credentials, generated artifacts, `.git`, and configured protected paths. |
| FR-105 | Apply a patch only when its base content/hash still matches; return the resulting diff. |
| FR-106 | Never discard unrelated working-tree changes. |
| FR-107 | Support branch, commit, push, and PR steps as separate approval-gated operations. |

### 6.2 dbt

| ID | Requirement |
|---|---|
| FR-201 | Detect dbt version, adapter, project file, profile, target, packages, and available commands. |
| FR-202 | Run `deps`, `parse`, `compile`, `list`, `test`, `run`, and `build` through a fixed command builder; no arbitrary flags or shell interpolation. |
| FR-203 | Resolve selectors before execution and report every selected node, target, database, and schema. |
| FR-204 | Reject source/target overlap and any target outside `dbt_write_datasets`. |
| FR-205 | Require A2 approval for state-changing dbt commands and A3 for production. |
| FR-206 | Default to development or ephemeral CI schemas, selected nodes, no `--full-refresh`, bounded threads, and a timeout. |
| FR-207 | Treat hooks, macros, packages, and model SQL as untrusted code; run only from the reviewed checkout and approved revision. |
| FR-208 | Parse `manifest.json`, `catalog.json`, `run_results.json`, and `sources.json` into concise structured evidence. |
| FR-209 | Capture stdout/stderr with secret filtering and retain artifact paths. |

### 6.3 Warehouse diagnostics

| ID | Requirement |
|---|---|
| FR-301 | List allowlisted projects, datasets, relations, schemas, partitioning, clustering, and metadata. |
| FR-302 | Accept only one read statement. Reject scripting, multi-statement SQL, DML, DDL, calls, exports, loads, remote functions, and unsupported constructs. |
| FR-303 | Perform a warehouse dry run before every query and enforce configured byte/cost limits. |
| FR-304 | Submit jobs with maximum bytes billed, timeout, labels, and query identity. |
| FR-305 | Limit returned rows, columns, cell size, and total serialized bytes; prefer aggregate evidence. |
| FR-306 | Allow only configured projects/datasets and reject wildcard traversal outside them. |
| FR-307 | Support query cancellation and return job ID, bytes processed, cache status, and truncated-result indicators. |
| FR-308 | Do not expose a generic BigQuery client or arbitrary job configuration. |

SQL parsing is defense in depth, not the permission boundary. IAM must still
make mutation impossible for the query identity.

### 6.4 Review, diagnosis, and fixes

| ID | Requirement |
|---|---|
| FR-401 | Build review context from changed files, referenced dbt nodes, upstream/downstream lineage, tests, contracts, compiled SQL, and bounded warehouse metadata. |
| FR-402 | Produce findings with severity, confidence, file/line evidence, affected nodes, and a reproducible validation command. |
| FR-403 | Generate a change plan before modification, including files, dbt selectors, query budget, risks, and rollback. |
| FR-404 | Apply the smallest patch, then run formatting/linting, parse/compile, targeted tests, and optionally an isolated dbt build. |
| FR-405 | Stop on policy failure, validation failure, stale files, unexpected selected nodes, target drift, or cost-budget breach. |
| FR-406 | Produce a final change report containing diff summary, commands, query job IDs, dbt artifacts, failures, and unverified assumptions. |

## 7. Public tool contract

Keep tools narrow and goal-oriented. The initial MCP surface should be:

| Tool | Writes state | Approval | Purpose |
|---|---:|---:|---|
| `project_list` | No | A0 | Return sanitized configured project aliases for first-use discovery. Proposed as an [additive v1 capability](specs/project-discovery.md). |
| `project_get` | No | A0 | Return sanitized project configuration and capabilities. |
| `repo_search` | No | A0 | Search allowlisted repository text. |
| `repo_read` | No | A0 | Read bounded file ranges. |
| `repo_status` | No | A0 | Return Git status/branch/diff summary. |
| `dbt_inspect` | No | A0 | Parse project/artifacts and return nodes/lineage/config. |
| `dbt_compile` | Generated local files | A1 | Compile selected resources and return compiled SQL/errors. |
| `dbt_test` | Warehouse reads and possible audit relations depending on project | A1/A2 based on resolved plan | Run targeted tests. |
| `warehouse_describe` | No | A0 | Return metadata for an allowlisted relation. |
| `warehouse_query` | No persistent data | A1 | Dry-run and execute one bounded read query. |
| `change_plan` | No | A0 | Validate and return a normalized proposed plan. |
| `repo_apply_patch` | Local files | A2 | Apply a hash-guarded patch inside allowed paths. |
| `dbt_execute` | dbt target relations | A2/A3 | Run/build selected dbt nodes using dbt-runner identity. |
| `change_validate` | May use dev/CI dbt target | A1/A2 | Run the configured validation ladder. |
| `git_publish` | Git/local or remote | A2/A3 | Branch, commit, push, or PR as explicit separate modes. |

There is intentionally no `shell`, `execute_sql`, `write_table`, `delete_file`,
`merge_pr`, or `deploy` tool.

Each tool response includes a stable operation ID, project ID, policy decision,
duration, sanitized evidence, truncation flags, and artifact references. Errors
are typed (`policy_denied`, `approval_required`, `invalid_input`,
`authentication_failed`, `budget_exceeded`, `execution_failed`, `timeout`,
`stale_workspace`) rather than returned as unstructured stack traces.

## 8. Core contracts and extension points

### 8.1 Warehouse provider SPI

```python
class WarehouseProvider(Protocol):
    def capabilities(self) -> WarehouseCapabilities: ...
    def describe_relation(self, ref: RelationRef) -> RelationMetadata: ...
    def dry_run(self, query: ReadQuery) -> QueryEstimate: ...
    def execute_read(self, query: ReadQuery, limits: QueryLimits) -> QueryResult: ...
    def cancel(self, job_id: str) -> None: ...
    def validate_dbt_boundary(self, plan: DbtExecutionPlan) -> PolicyDecision: ...
```

The core uses normalized relation references and results. BigQuery-specific job
IDs, bytes processed, location, partition metadata, and labels live in provider
extensions. Future Snowflake, Databricks, Redshift, or DuckDB providers must
implement the same safety semantics or explicitly report unsupported
capabilities.

### 8.2 dbt adapter

The dbt adapter is warehouse-neutral and shells out without a shell to the
project's pinned `dbt` executable or approved container wrapper. It owns:

- command/flag allowlists;
- selector resolution;
- target and dataset verification;
- artifact parsing;
- timeouts and process cancellation; and
- detection of state-changing hooks/macros before approval.

### 8.3 Policy engine

The policy engine is deterministic and model-independent. It receives an
operation plus fully resolved targets and returns allow, deny, or
approval-required with machine-readable reasons. Policies cover filesystem,
Git, SQL shape, warehouse resources, query cost, dbt commands, environments,
and result disclosure.

## 9. Configuration contract

A checked-in `dbt-agents.yml` holds non-secret policy. Environment variables or
the platform credential chain select credentials.

```yaml
version: 1
projects:
  keylo:
    repo_root: /Users/gordonsilvera/Repos/theDataStrategist/keylo
    dbt_project_dir: keylo-dbt
    writable_paths:
      - keylo-dbt/models/**
      - keylo-dbt/macros/**
      - keylo-dbt/tests/**
      - keylo-dbt/seeds/**
      - keylo-dbt/snapshots/**
      - keylo-dbt/analyses/**
      - keylo-dbt/docs/**
      - keylo-dbt/specs/**
      - keylo-dbt/dbt_project.yml
      - keylo-dbt/packages.yml
    protected_paths:
      - "**/.env*"
      - "**/*credential*"
      - "**/*service-account*"
      - "**/*.pem"
      - "**/*.key"
      - "**/.git/**"
    dbt:
      executable: dbt
      profiles_dir: keylo-dbt/profiles
      default_target: dev
      production_targets: [prod]
      allowed_commands: [deps, parse, compile, list, test, run, build]
      allow_full_refresh: false
    warehouse:
      provider: bigquery
      project: verdant-abacus-481415-d2
      location: US
      read_datasets: [vertex_dev, keylo_dbt_staging, keylo_dbt_intermediate,
                      keylo_dbt_marts, keylo_dbt_audit, keylo_dbt_ci]
      dbt_write_datasets: [keylo_dbt_staging, keylo_dbt_intermediate,
                           keylo_dbt_marts, keylo_dbt_audit, keylo_dbt_ci]
      forbidden_write_datasets: [vertex_dev]
      auth:
        mode: impersonation
        query_service_account_env: DBT_AGENTS_QUERY_SERVICE_ACCOUNT
        dbt_service_account_env: DBT_AGENTS_DBT_SERVICE_ACCOUNT
    limits:
      query_max_bytes_billed: 1000000000
      query_timeout_seconds: 60
      query_max_rows: 1000
      query_max_result_bytes: 2000000
      dbt_timeout_seconds: 900
```

Before implementation, this YAML shape becomes a versioned JSON Schema. Paths
may use environment interpolation, but secret values may not. Relative paths
resolve against the configuration file. Configuration loading fails closed on
unknown keys, invalid globs, overlapping source/target datasets, missing
projects, or an unrecognized provider.

## 10. Primary workflows

### 10.1 Repository and SQL review

1. Resolve project and Git state.
2. Parse dbt manifest or generate it with `dbt parse`.
3. Determine changed nodes and lineage neighborhood.
4. Read relevant source, schema YAML, macros, tests, contracts, and compiled SQL.
5. Optionally inspect allowlisted warehouse metadata and dry-run compiled SQL.
6. Return evidence-backed findings; make no edits unless requested.

### 10.2 Diagnose a data problem

1. Restate the symptom and identify the affected dbt resource.
2. Trace lineage upstream using the manifest.
3. Form a bounded query plan with estimated bytes and row/result limits.
4. Run read-only aggregate queries under the query identity.
5. Attribute the likely fault to source data, transformation logic, contract,
   freshness, or orchestration, with job IDs and query text.
6. Propose a code/test fix; never repair data directly.

### 10.3 Automated code fix

1. Create a normalized change plan and obtain A2 approval.
2. Confirm clean/understood Git state and hash the files to edit.
3. Apply the minimal patch.
4. Run the validation ladder: format/lint, parse, compile, targeted unit/data
   tests, then an isolated selected dbt build if needed.
5. On failure, retain the patch and explain the failure; never erase unrelated
   user work.
6. With separate approval, create a branch/commit. Push and PR are separate A3
   actions.

### 10.4 Run a dbt model

1. Resolve selector to exact nodes before asking for approval.
2. Compile and inspect target database/schemas plus hooks.
3. Reject any target outside the allowlist or any source/target collision.
4. Display command, nodes, environment, identity, timeout, and rollback.
5. Execute without a shell under the dbt-runner identity.
6. Parse artifacts and report created/changed relations and test results.

## 11. Local, ChatGPT, Codex, and CI deployment

### 11.1 Local-first package

Recommended stack:

- Python 3.11+;
- official Python MCP SDK;
- Pydantic for strict configuration and tool schemas;
- Typer for the CLI;
- dbt Core invoked as a pinned external/runtime dependency;
- Google Cloud BigQuery/auth libraries in an optional `bigquery` extra;
- `sqlglot` plus provider dry runs for SQL classification; and
- no database or daemon requirement.

Package extras keep installation light: `dbt-agents[bigquery,mcp]`. A container
is optional for reproducibility, not mandatory for local use.

### 11.2 Codex

Run the MCP server locally over stdio or loopback HTTP. Codex operates in the
same checkout and can call the narrow tools. The server remains useful even
when Codex also has filesystem access because warehouse credentials and safety
policy stay centralized.

### 11.3 ChatGPT web

Run the streamable HTTP endpoint on the user's machine and connect it through
a secure MCP tunnel. This preserves local repository and ADC access without a
public server. A future public plugin can use a hosted HTTPS endpoint, but a
hosted service cannot access a laptop checkout without a runner or Git provider
integration and therefore is outside v1.

### 11.4 GitHub Actions

Use the CLI for deterministic checks:

```text
dbt-agents review --project keylo --base origin/main --format sarif
dbt-agents validate --project keylo --changed-only
```

CI uses GitHub OIDC/workload identity, a read-only checkout, and a unique CI
dataset/schema. Initial CI should run policy, lint, parse, compile, and targeted
tests. LLM-generated review in CI is optional and separate because it adds API
credentials, cost, nondeterminism, and possible source-code disclosure.

## 12. Safety and privacy requirements

| ID | Requirement |
|---|---|
| SEC-01 | Treat repository files, SQL comments, warehouse strings, dbt docs, packages, and tool output as untrusted data, never as agent instructions. |
| SEC-02 | Never return credentials, environment values, auth headers, service-account JSON, `.env` contents, or secret-like strings. |
| SEC-03 | Deny paths before reading, not only before returning content. |
| SEC-04 | Permit configurable dataset, table, column, row, and result-redaction policies. |
| SEC-05 | Default to schema/aggregate evidence; raw row sampling must be independently configurable and disabled for protected columns. |
| SEC-06 | Log operation metadata and hashes, not complete query results or file contents. |
| SEC-07 | Pin dependencies and scan releases; dbt packages and macros execute code and are part of the trust boundary. |
| SEC-08 | Bind local HTTP to loopback, require a session token, validate origins where applicable, and use the secure tunnel for ChatGPT. |
| SEC-09 | Fail closed if SQL classification, identity selection, target resolution, or policy evaluation is uncertain. |
| SEC-10 | Apply server-side budgets even if the client requests higher limits. |

## 13. Audit record

Append JSON Lines locally under a configurable, ignored state directory. Each
record contains:

- operation ID, timestamp, tool/version, project, actor/session, and approval;
- Git revision and dirty-state hash;
- sanitized parameters and resolved targets;
- credential *identity name* but never credential material;
- policy rule IDs and decision;
- command/query hash, BigQuery job ID, bytes processed, dbt invocation ID;
- exit status, duration, artifact hashes, and truncation flags.

Logs rotate and have a configurable retention period. Query results and source
file bodies are excluded by default.

## 14. Reliability and observability

- All operations have deadlines and cooperative cancellation.
- A cancelled client request attempts to cancel its BigQuery job or subprocess.
- Read operations may retry transient provider failures with bounded backoff;
  state-changing dbt commands are not blindly retried.
- Every process captures exit code and bounded output.
- Startup performs a capability check: repository, dbt executable, profile,
  credentials, warehouse location, and IAM boundary.
- Health output distinguishes configuration, authentication, authorization,
  dependency, and network failures.
- OpenTelemetry integration is optional; local structured logs are sufficient
  for v1.

## 15. Test and evaluation plan

### 15.1 Unit and contract tests

- path traversal, symlink escape, ignore rules, protected-file detection;
- SQL parser bypass corpus: comments, semicolons, scripts, CTEs, DDL/DML,
  procedures, exports, remote functions, and dialect edge cases;
- configuration validation and provider contract tests;
- command argument injection and selector resolution;
- result truncation/redaction and secret filtering;
- approval-level and environment-policy decisions;
- MCP schema and annotation snapshots.

### 15.2 Integration tests

- temporary Git/dbt fixture repository with dirty user changes;
- BigQuery sandbox containing source, dbt dev, and CI datasets;
- prove query identity cannot write to any dataset;
- prove dbt runner cannot write to source and can write only to dbt targets;
- compile/test/run one selected fixture model and validate artifacts;
- cancel an expensive query and timed-out dbt process;
- local stdio MCP, local HTTP MCP, and CLI parity.

### 15.3 Agent evaluations

Maintain versioned scenarios with expected tool calls and outcomes:

- review a faulty join without editing;
- diagnose a duplicate-row regression using bounded queries;
- add a dbt test and validate it;
- reject a request to update source data directly;
- reject a model targeting the source dataset;
- preserve unrelated local changes;
- stop before push/PR/production without approval;
- ignore prompt injection embedded in SQL comments or table values;
- switch from BigQuery project A to project B solely through config.

## 16. Acceptance criteria for v1

| ID | Criterion |
|---|---|
| AC-01 | A user can configure and inspect the Keylo dbt project without source changes. |
| AC-02 | Codex can connect locally and ChatGPT web can connect through a secure tunnel to the same MCP tools. |
| AC-03 | The agent reviews a changed dbt model with manifest lineage, compiled SQL, tests, and file/line evidence. |
| AC-04 | `warehouse_query` executes bounded `SELECT`/`WITH` diagnostics and rejects every mutation fixture. |
| AC-05 | IAM integration tests prove the query identity cannot write and the dbt identity cannot write outside configured dbt targets. |
| AC-06 | With approval, the agent patches a dbt model/test and passes the configured validation ladder. |
| AC-07 | With approval, `dbt_execute` runs a selected model in a dev/CI target and reports artifacts and affected relations. |
| AC-08 | No tool offers arbitrary shell or direct data mutation. |
| AC-09 | GitHub Actions runs deterministic policy and validation checks with workload identity and an isolated CI dataset. |
| AC-10 | A second BigQuery project can be selected by configuration only. |
| AC-11 | Provider contract tests can run against a stub second provider without changing core workflows. |
| AC-12 | Security tests cover path escape, command injection, SQL bypass, prompt injection, secrets, cost limits, and stale-workspace writes. |

## 17. Proposed repository layout

```text
dbt-agents/
  README.md
  pyproject.toml
  src/dbt_agents/
    cli.py
    server.py
    config.py
    models.py
    policy/
    workflows/
    adapters/
      repo.py
      git.py
      dbt.py
    providers/
      base.py
      bigquery.py
    tools/
    audit.py
  schemas/
    config.schema.json
  tests/
    unit/
    integration/
    fixtures/
  evals/
    cases.yml
  docs/
    design-spec.md
    threat-model.md
    provider-authoring.md
  examples/
    keylo.dbt-agents.yml
  .github/workflows/
    ci.yml
```

## 18. Delivery plan

### Phase 0: contracts and threat model

- Approve this specification.
- Define JSON Schema, normalized data types, provider protocol, policy rules,
  and explicit threat model.
- Create malicious-input fixtures before implementing executors.

### Phase 1: read-only review MVP

- Config loader, repository/Git adapters, dbt artifact parser.
- BigQuery metadata, dry run, and read-only query provider.
- CLI plus MCP read tools.
- Keylo example configuration and review evals.

### Phase 2: safe fixes

- Change plans, hash-guarded patching, validation ladder.
- Approval flow and audit log.
- Local branch/commit operations; optional push/PR after separate approval.

### Phase 3: dbt execution

- Separate dbt runner identity.
- Selector/target preflight and dev/CI `run`/`build`.
- BigQuery IAM boundary integration tests.

### Phase 4: distribution and CI

- Python package/extras, container, templates, documentation.
- Secure-tunnel ChatGPT guide and Codex config.
- Deterministic GitHub Action and SARIF/check annotations.

### Phase 5: extensibility

- Provider-authoring kit and conformance suite.
- Implement one small second provider, likely DuckDB, to prove the abstraction.
- Add optional specialized agent profiles only if evaluations show measurable
  benefit over one orchestrating agent.

## 19. Trade-offs and alternatives

| Decision | Benefit | Cost / limitation |
|---|---|---|
| Local-first MCP + CLI | Lightweight; repository and ADC remain local; works with Codex and ChatGPT tunnel | User must run a local process/tunnel. |
| One agent, deterministic tools | Smaller attack surface and simpler evaluation | Less role specialization initially. |
| Two cloud identities | Strong enforcement of dbt-only writes | More IAM setup; ADC-only mode is weaker. |
| Python | Natural fit with dbt Core and BigQuery; quick open-source contribution path | Dependency conflicts require isolation/pinning. |
| No vector DB | Minimal operations and fewer stale indexes | Very large monorepos may eventually need indexing. |
| CLI-first GitHub Action | Deterministic and credential-light | Automated prose/code review needs an optional model-backed job. |
| Provider SPI | Enables other warehouses | Safety semantics vary and require conformance tests. |

## 20. Frozen v1 decisions

The package/configuration names, query budget, raw-row policy, authentication
modes, dbt-test approval, Git publishing boundary, and exact support guarantee
are accepted in [ADR-0001](adr/0001-v1-contract-freeze.md). The normative tool
and configuration inventory is [the v1 contract](contracts/v1.md).

The remaining question about adding configuration or workflows to `keylo-dbt`
is an integration-scope decision, not part of this public contract.

## 21. Growth triggers

Revisit the design when any of these occur:

- more than one concurrent user needs the same installation;
- repositories are too large for bounded search and manifest context;
- long-running jobs require durable queues and resumable workflows;
- production execution becomes scheduled or unattended;
- tenant-specific OAuth and hosted Git access are required;
- a second warehouse exposes incompatible safety semantics; or
- evaluations show that specialized agents outperform one orchestrator enough
  to justify coordination complexity.

At that point, split the stateless MCP/API gateway from isolated per-job
runners and add durable operation state. Do not introduce that infrastructure
for the local single-user v1.
