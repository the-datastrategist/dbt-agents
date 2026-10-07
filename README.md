# dbt-agents

`dbt-agents` is an open-source, local-first toolkit for reviewing, querying,
testing, and safely fixing dbt projects and their warehouses with AI agents.

The initial warehouse implementation targets BigQuery, while the core contracts
are designed for additional adapters. The primary interfaces are an MCP server
for ChatGPT/Codex and a CLI for local use and GitHub Actions.

The same policy-enforced core powers:

- a Model Context Protocol server for ChatGPT and Codex;
- a local CLI for inspection and diagnostics; and
- deterministic automation suitable for GitHub Actions.

Direct warehouse queries are read-only. dbt is the only tool path permitted to
write warehouse relations, and only in explicitly allowlisted dbt datasets.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[all,dev]'
cp examples/keylo.dbt-agents.yml dbt-agents.yml
.venv/bin/dbt-agents check-config --config dbt-agents.yml
.venv/bin/dbt-agents project keylo --config dbt-agents.yml
```

See [Getting started](docs/getting-started.md) for authentication, Codex, and
ChatGPT setup. The architecture and acceptance criteria are in the
[design specification](docs/design-spec.md).

## Safety model

- strict repository roots and writable-path allowlists;
- protected credential and environment files;
- SHA-guarded atomic file replacement;
- SQL AST validation, one-statement enforcement, dry runs, byte budgets, row
  limits, and IAM read-only query credentials;
- exact short-lived plans plus approval for writes;
- separate query and dbt-runner identities;
- fixed dbt and Git command builders with no arbitrary shell tool; and
- sanitized JSONL audit events without file bodies or query results.

The initial warehouse provider is BigQuery. Provider contracts are intentionally
small so other adapters can implement the same safety semantics.
