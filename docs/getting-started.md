# Getting started

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[all,dev]'
cp examples/keylo.dbt-agents.yml dbt-agents.yml
.venv/bin/dbt-agents check-config --config dbt-agents.yml
```

Keep `dbt-agents.yml` local when it contains machine-specific paths. It must
never contain credential material.

Configuration `version: 1` uses a 1 GB per-query ceiling, 60-second query
timeout, and 100 returned rows by default. Raw physical-table rows are disabled
unless a project explicitly sets `warehouse.allow_raw_rows: true`; aggregate
diagnostics remain available. See the [v1 contract](contracts/v1.md).

## Authentication

For a quick local setup, authenticate application-default credentials:

```bash
gcloud auth application-default login
```

For stronger separation, set `warehouse.auth.mode: impersonation`, configure
the two service-account environment-variable names, and export the account
addresses. The logged-in ADC principal needs permission to impersonate them.

Service-account JSON is a compatibility mode. Store both files outside every
repository and point `DBT_AGENTS_QUERY_CREDENTIALS` and
`DBT_AGENTS_DBT_CREDENTIALS` to distinct read-only-query and dbt-runner keys.
Never copy either file into project configuration.

For dbt service-account impersonation, the selected dbt profile must contain:

```yaml
impersonate_service_account: "{{ env_var('DBT_AGENTS_DBT_IMPERSONATE_SERVICE_ACCOUNT') }}"
```

The server verifies this before starting dbt in impersonation mode.

GitHub Actions should use OIDC/workload identity and no downloaded key.

## CLI

The argument `keylo` below is the dbt-agents project alias: the key under
`projects` in `dbt-agents.yml`. It is not the BigQuery/GCP project ID. For
example, alias `keylo` may target warehouse project
`verdant-abacus-481415-d2`.

```bash
export DBT_AGENTS_CONFIG="$PWD/dbt-agents.yml"
dbt-agents projects
dbt-agents project keylo
dbt-agents status keylo
dbt-agents search keylo "ref(" keylo-dbt/models
dbt-agents dbt-inspect keylo
dbt-agents warehouse-query keylo \
  'select count(*) as rows from `verdant-abacus-481415-d2.vertex_dev.transactions`' \
  --dry-run
```

MCP clients can call the no-argument `project_list` tool before `project_get`
or any other project-scoped operation. The tool returns sanitized aliases and
does not load warehouse credentials or expose local paths. See the
[project discovery specification](specs/project-discovery.md).

After selecting an alias, run `project-readiness` (or the MCP
`project_readiness` tool) before compile or dbt execution to identify unresolved
`var()` and environment requirements without running dbt:

```bash
dbt-agents project-readiness keylo --config dbt-agents.yml
```

## Onboard a new dbt repository

Use the CLI-first onboarding flow before a repository is added to an active
configuration. It does not read `.env` files or credential material, and the
initial inspection makes no network or warehouse calls.

```bash
dbt-agents onboard inspect \
  --repo-root /work/acme \
  --dbt-project-dir /work/acme/dbt \
  --output .dbt-agents/onboarding/acme/report.json
```

Review the report's unresolved `var()` and `env_var()` requirements. Supply
only non-secret business settings in an answers file such as:

```yaml
alias: acme
warehouse_project: acme-analytics
source_datasets: [raw]
read_datasets: [raw, analytics_dev, analytics_ci]
dbt_write_datasets: [analytics_dev, analytics_ci]
target_datasets:
  dev: analytics_dev
  ci: analytics_ci
dbt_environment:
  DBT_SOURCE_PROJECT: acme-analytics
  DBT_SOURCE_DATASET: raw
auth_mode: impersonation
query_service_account_env: DBT_AGENTS_QUERY_SERVICE_ACCOUNT
dbt_service_account_env: DBT_AGENTS_DBT_SERVICE_ACCOUNT
```

Generate and validate an inactive candidate before registration:

```bash
dbt-agents onboard propose \
  --report .dbt-agents/onboarding/acme/report.json \
  --answers onboarding-answers.yml \
  --output .dbt-agents/onboarding/acme/candidate.yml

dbt-agents onboard validate \
  --candidate .dbt-agents/onboarding/acme/candidate.yml
```

Add `--compile` to run `dbt parse` and `dbt compile`; add `--with-deps` when
the project declares packages. Add `--live` only after configuring credentials
to run live readiness checks. Neither mode runs models, builds relations, or
changes IAM.

Registration is a two-step, hash-guarded operation. First inspect the exact
configuration diff and its `active_config_sha256`; then approve that unchanged
configuration explicitly:

```bash
dbt-agents onboard register --candidate .dbt-agents/onboarding/acme/candidate.yml \
  --config dbt-agents.yml

dbt-agents onboard register --candidate .dbt-agents/onboarding/acme/candidate.yml \
  --config dbt-agents.yml --expected-sha256 '<preview hash>' --approve
```

Restart the local MCP server or tunnel after a successful registration, then
call `project_list` from ChatGPT or Codex. See the
[project onboarding specification](specs/project-onboarding.md) for the
security model and implementation details.

## Codex

Install the package and merge the example from
`examples/codex-config.toml` into `~/.codex/config.toml`. Use absolute paths so
Codex can start the stdio process reliably. For the Keylo two-identity setup,
configure the query and dbt-runner service-account email environment variables
as shown in [Operational readiness](operations.md#local-impersonation), then
reload Codex before expecting the server to appear.

## ChatGPT web

Generate a strong bearer token, start loopback streamable HTTP, and connect the
endpoint through ChatGPT's Secure MCP Tunnel:

```bash
export DBT_AGENTS_HTTP_TOKEN='<random secret>'
dbt-agents serve --config dbt-agents.yml \
  --transport streamable-http --host 127.0.0.1 --port 8765
```

The MCP endpoint is `http://127.0.0.1:8765/mcp`. Configure the tunnel/client to
send `Authorization: Bearer <random secret>`. The server refuses non-loopback
bindings in v1 and refuses unauthenticated HTTP unless the explicitly unsafe
development flag is supplied. Keep the local process running for the life of
the tunnel, and never place the bearer token in the endpoint URL, repository
configuration, or a client prompt.

OpenAI's current documentation describes stdio and environment-local HTTP MCP
connections and recommends absolute working directories for stdio. See
[MCP connections](https://developers.openai.com/api/docs/guides/agents-api/tools/mcp).

## Write workflow

Every write uses a plan, a trusted local approval, and the matching write call:

1. `change_plan(project, action, details)` returns a short-lived `plan_id`.
2. After reviewing it, approve the exact plan outside MCP with
   `dbt-agents approve-plan PROJECT ACTION 'DETAILS_JSON' --plan-id PLAN_ID --approve`.
3. Call the matching write tool with the same details, the `plan_id`, and
   `approved=true`.

The service consumes a plan on the first attempt. Changed arguments, an expired
plan, or a reused plan are rejected. Repository replacement additionally needs
the SHA-256 returned by `repo_read` and a `content_sha256` for the proposed UTF-8
replacement body. This prevents stale overwrites and post-approval substitution.
Local CLI write commands persist this approval automatically when `--approve`
is supplied; MCP intentionally has no plan-approval tool.
