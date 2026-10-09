# Threat model

## Assets

- repository source and uncommitted user changes;
- warehouse data, metadata, job quota, and billing;
- cloud and Git credentials;
- dbt-owned production relations; and
- audit integrity.

## Trust boundaries

The model, tool inputs, repository contents, dbt packages/macros, SQL comments,
and warehouse values are untrusted. The deterministic policy engine, credential
selection, fixed command builders, and cloud IAM form the enforcement boundary.
MCP annotations improve client behavior but are not authorization.

## Principal threats and controls

| Threat | Control |
|---|---|
| Path traversal or symlink escape | Canonical path containment, protected components, no followed search symlinks, writable globs. |
| Credential disclosure | Pre-read protected paths, split credential environment names, minimal dbt subprocess environments, result/log redaction, no environment inspection tool. |
| Stale overwrite | Required SHA-256 match and atomic file replacement. |
| Shell/argument injection | No shell execution; fixed argv builders; selector and identifier validation. |
| Direct data mutation | Read-only SQL AST policy plus a query identity without data-write IAM. |
| SQL parser bypass | One parsed BigQuery statement, forbidden AST/text corpus, output-shape restrictions for protected/raw rows, provider dry run, IAM backstop. |
| Excessive query cost | Dry run, maximum bytes billed, timeout, row/cell/result limits, cancellation. |
| dbt writes to source | Explicit target mapping, resolved-node database/schema preflight, dbt identity scoped to target datasets. |
| Prompt injection in code/data | Server instructions identify outputs as untrusted; tools never derive authorization from content. |
| Unauthorized write | Short-lived exact plan, content digest, out-of-band local approval unavailable to MCP, accurate annotation, atomic one-use consumption. |
| Unintended remote Git action | Separate branch/commit/push/PR modes; no merge, force push, or arbitrary Git arguments. |
| Unauthenticated local HTTP | Loopback-only binding and bearer token by default. |
| Dependency/macro compromise | Pinned release ranges, minimal subprocess environment, no-introspection compile, reviewed checkout, CI, future dependency scanning. |

## Residual risks

- ADC mode may have more IAM privilege than the server policy requires. Prefer
  impersonation with separate query and dbt-runner service accounts.
- dbt macros execute trusted project/package code. Review dependency and macro
  changes before granting a dbt execution approval.
- SQL dialects and warehouses evolve. Keep the bypass corpus current and rely
  on IAM as the final mutation boundary.
- A model can misinterpret non-sensitive query results. Require evidence and
  human review for production decisions.
