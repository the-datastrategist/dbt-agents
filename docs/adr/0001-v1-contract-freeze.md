# ADR-0001: Freeze the v1 public contract

**Status:** Accepted  
**Date:** 2026-10-07  
**Deciders:** Project maintainer

## Context

Client integration, live warehouse testing, and agent evaluations all depend on
stable tool names, configuration fields, safety defaults, and support claims.
Changing those contracts after users configure Codex, ChatGPT, or CI would
create avoidable migration work and could weaken policy enforcement.

This decision freezes the public v1 contract without claiming that every later
operational milestone is complete. Additive optional fields and new tools remain
possible. Removing or renaming a tool, changing a field's meaning, or weakening
a default requires a new contract version and migration guide.

## Decision

### 1. Names

- Distribution and repository: `dbt-agents`
- Python import: `dbt_agents`
- CLI: `dbt-agents`
- MCP executable: `dbt-agents-mcp`
- MCP server name: `dbt-agents`
- Configuration file: `dbt-agents.yml`
- Local-only override convention: `dbt-agents.local.yml`
- Environment prefix: `DBT_AGENTS_`
- Configuration contract discriminator: `version: 1`

Both `dbt-agents` and `dbt-agent` were unclaimed on PyPI when checked on the
decision date. `dbt-agents` matches the repository, allows multiple future
agent profiles, and avoids the singular CLI used by adjacent projects.

### 2. Query budget

- Maximum bytes billed: **1,000,000,000 bytes per query** (decimal 1 GB).
- Every execution requires a successful dry run first.
- A query estimated above the project limit is rejected, not merely confirmed.
- Default execution timeout: 60 seconds.
- Default returned rows: 100.
- Default serialized result: 2,000,000 bytes.
- Projects may lower any limit. Raising one is a reviewed configuration change,
  not a runtime argument.
- v1 does not enforce daily or monthly spend budgets; cloud billing budgets and
  alerts remain a separate control.

The byte ceiling is authoritative. Dollar estimates are excluded because
warehouse pricing and billing models change independently of this tool.

### 3. Raw-row policy

- `allow_raw_rows` defaults to `false`.
- By default, physical-table queries must contain an aggregate function.
- When explicitly enabled, configured row, cell, and result limits still apply.
- `protected_columns` are rejected even when raw rows are enabled.
- Schema and relation metadata remain available through `warehouse_describe`.
- The Keylo example enables raw rows because its owner declared the current data
  non-sensitive; this is an example-specific exception.

### 4. Authentication modes

v1 supports exactly four modes:

| Mode | Supported use | Guarantee |
|---|---|---|
| `adc` | Local development | Functional convenience; may be over-privileged and is lower assurance. |
| `impersonation` | Recommended local mode | Separate query and dbt-runner service accounts without stored keys. |
| `service_account_file` | Compatibility | Requires distinct external query/dbt key paths; keys must not be committed. |
| `workload_identity` | CI and hosted runners | Uses runtime-provided short-lived credentials; no downloaded key. |

The query identity must be unable to write warehouse data. The dbt-runner
identity may write only configured dbt-owned datasets. Authentication selects
credentials; deterministic policy and warehouse IAM remain authoritative.

### 5. `dbt test` approval

Every `dbt test` requires write-level approval: A2 for development and CI, and
A3 for configured production targets. There is no read-only exception in v1.
Tests can invoke macros/hooks, create temporary resources, or persist audit
relations, and static classification cannot safely downgrade them.

### 6. Git publishing

The initial implementation uses installed `git` and GitHub CLI (`gh`) commands.
It exposes only four modes:

| Mode | Approval | Boundary |
|---|---:|---|
| `branch` | A2 | Create one validated local branch. |
| `commit` | A2 | Commit only explicit allowlisted paths. |
| `push` | A3 | Push the current/named branch to `origin`; never force. |
| `pr` | A3 | Create a pull request with explicit title/body/base. |

Each mode needs its own exact one-use plan. v1 does not expose merge, close,
delete, rebase, reset, force-push, tag, release, or arbitrary Git/GitHub
arguments. GitHub API support may later sit behind the same contract.

### 7. Exact v1 support guarantee

The v1 compatibility target is:

- Python 3.11, 3.12, and 3.13;
- dbt Core `>=1.10,<1.12`;
- BigQuery via `dbt-bigquery >=1.10,<1.12`;
- local single-user execution on macOS and Linux;
- Codex and ChatGPT-compatible MCP over stdio or authenticated loopback
  streamable HTTP;
- local CLI execution;
- GitHub.com publishing through `git` and `gh`;
- GitHub Actions with workload identity;
- configuration contract `version: 1`; and
- the original 13 MCP tools listed when this decision was accepted, plus
  backward-compatible additive tools recorded in `docs/contracts/v1.md`.

v1 does not guarantee Windows, dbt Cloud, other warehouses, hosted
multi-tenancy, public MCP hosting, unattended production execution, arbitrary
SQL/shell/Git, or automated PR merging.

## Options considered

### Keep decisions configurable but undefined

Lower commitment, but clients and tests would encode accidental behavior.
Rejected because security-sensitive defaults need normative meaning.

### Freeze only tool names

Smaller promise, but configuration and policy drift could still break clients
or weaken safety. Rejected.

### Freeze names, schemas, defaults, and support boundaries

Creates more maintenance responsibility, but gives integrations a clear target
and makes breaking changes reviewable. Accepted.

## Consequences

- Client and evaluation work can depend on stable v1 names and meanings.
- Safer defaults may require explicit project exceptions.
- New optional fields and tools are allowed; breaking changes require
  `version: 2` or a separately versioned tool contract.
- Package release versioning remains independent from configuration contract
  versioning while later operational milestones are completed.

## Action items

- [x] Publish the normative v1 contract.
- [x] Align code and JSON Schema defaults.
- [x] Add snapshot tests for MCP tool names and policy defaults.
- [x] Test all supported Python versions in CI.
- [ ] Publish a migration guide only when a v2 change is proposed.
