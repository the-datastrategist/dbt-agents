# Project discovery (`project_list`)

| Field | Value |
|---|---|
| Status | Proposed |
| Date | 2026-10-09 |
| Target release | Additive v1 minor release |
| Interfaces | MCP first; CLI parity as a follow-up |
| Safety level | A0 (read-only inspection) |

## Problem statement

Every current MCP operation requires a dbt-agents project identifier, but the
server does not expose a way to discover the identifiers configured under the
top-level `projects` mapping. An agent that knows only a warehouse project such
as `verdant-abacus-481415-d2` cannot infer that the required dbt-agents alias is
`keylo`; trying the warehouse ID or an empty string returns `unknown project`.

This creates a dead end during first use and whenever one server exposes more
than one configured project. It also encourages clients to guess identifiers or
hard-code deployment-specific aliases in prompts.

## Goals

- Let an MCP client discover valid project aliases without prior deployment
  knowledge.
- Make the distinction between a dbt-agents alias and a warehouse project ID
  explicit.
- Keep discovery deterministic, credential-free, and safe to invoke without
  approval.
- Give agents enough information to select an alias, then use `project_get` for
  detailed boundaries and capabilities.

## Non-goals

- Discover repositories, dbt projects, or cloud projects that are not already
  present in the loaded configuration.
- Probe BigQuery, initialize credentials, or validate live warehouse access.
- Return local filesystem paths, dataset allowlists, credential environment
  variable names, or other detailed configuration. `project_get` remains the
  detailed inspection operation.
- Accept a warehouse project ID as an implicit substitute for a configured
  alias. Alias selection stays explicit to avoid ambiguity.
- Add, remove, or edit configured projects.

## User stories

- As a ChatGPT or Codex user, I want the agent to discover configured project
  aliases so that I do not need to know an implementation-specific identifier.
- As an agent, I want a no-argument discovery operation so that I can call it
  before any project-scoped tool.
- As an operator with multiple configured projects, I want each alias paired
  with its warehouse provider and warehouse project ID so that the correct
  scope is unambiguous.
- As a security-conscious operator, I want discovery to avoid credentials,
  network calls, and local paths so that listing projects has minimal exposure.

## Proposed MCP contract

### Tool

```text
project_list() -> ProjectListResult
```

The tool has no arguments. It is annotated with:

- `readOnlyHint=true`
- `destructiveHint=false`
- `openWorldHint=false`

The title is **List configured dbt projects**. Its description should tell the
agent to call it when a project alias is unknown and to pass the returned
`id` to project-scoped tools such as `project_get`.

### Response

```json
{
  "ok": true,
  "operation_id": "<uuid>",
  "operation": "project_list",
  "project": null,
  "policy": {
    "decision": "allow",
    "level": 0,
    "rule_ids": ["PROJECT-LIST"],
    "reasons": []
  },
  "duration_ms": 0,
  "data": {
    "count": 1,
    "projects": [
      {
        "id": "keylo",
        "warehouse_provider": "bigquery",
        "warehouse_project": "verdant-abacus-481415-d2",
        "dbt_default_target": "dev"
      }
    ]
  },
  "warnings": [],
  "truncated": false
}
```

`project` is `null` because the operation is configuration-scoped rather than
project-scoped. Existing project-scoped operations continue returning a string.
The compatibility change therefore applies only to the new result type; it
must not loosen the existing `OperationResult.project` contract.

Projects are sorted lexicographically by `id`. The response contains exactly:

- `id`: key in the configuration's `projects` mapping;
- `warehouse_provider`: configured provider name;
- `warehouse_project`: provider project/account identifier; and
- `dbt_default_target`: configured default dbt target.

No local path, dataset, protected-path, authentication, environment-variable,
or credential information is returned.

### Errors

- Invalid configuration prevents server startup as it does today; the tool does
  not introduce a second configuration-validation path.
- The tool must not emit `unknown project`, because it accepts no project input.
- Unexpected failures use the existing typed `execution_failed` response and
  sanitized error handling.

## Requirements

### P0 — required to ship

| ID | Requirement | Acceptance criterion |
|---|---|---|
| PD-001 | Expose no-argument `project_list` through MCP. | Given a valid configuration, when the MCP catalog is inspected, then `project_list` exists with no required inputs. |
| PD-002 | Return every configured alias in deterministic order. | Given two aliases in reverse configuration order, when called, then both appear sorted by `id`. |
| PD-003 | Distinguish alias from warehouse project. | Given alias `keylo` targeting `verdant-abacus-481415-d2`, then both values appear in their separately named fields. |
| PD-004 | Minimize disclosed configuration. | The result contains only the four documented project fields and no paths, datasets, auth settings, or environment-variable names. |
| PD-005 | Avoid side effects and external access. | Calling the tool does not instantiate a project runtime, load warehouse credentials, call a provider, write an audit file, or access the network. |
| PD-006 | Preserve the safety contract. | The MCP annotations are read-only/non-destructive, policy level is A0, and the tool cannot request approval or mutate state. |
| PD-007 | Teach the MCP server to use discovery first. | Server instructions direct agents to call `project_list` when an alias is unknown and then call `project_get` with a returned `id`. |
| PD-008 | Cover the first-use failure in an evaluation. | Given no alias in the prompt, the expected call sequence is `project_list` followed by `project_get`; no guessed or empty project value is sent. |

### P1 — fast follow

| ID | Requirement | Acceptance criterion |
|---|---|---|
| PD-101 | Add CLI parity with `dbt-agents projects`. | The command prints the same sorted, sanitized `data` payload without initializing credentials. |
| PD-102 | Improve unknown-project errors. | A project-scoped call with an invalid alias recommends `project_list` without disclosing additional configuration. |

### P2 — future considerations

- Optional human-readable project descriptions in configuration and discovery.
- Provider-neutral scope fields for warehouses that do not use a cloud project
  concept; do not add them until a second provider establishes the abstraction.
- Optional filtering only if project catalogs become large. The v1 operation
  remains no-argument and returns all configured aliases.

## Technical design

1. Add a configuration-scoped service method that reads `self.config.projects`
   directly and builds sanitized summaries. It must not call `_runtime()`.
2. Use a dedicated result model with `project: None`, rather than changing the
   existing project-scoped `OperationResult` model.
3. Register `project_list` before `project_get` in the MCP catalog and add
   explicit safety annotations.
4. Update server instructions, tool-name/input-schema snapshots, and contract
   documentation as an additive v1 tool.
5. Add CLI parity after the MCP behavior is stable.

This is permitted by the v1 compatibility rule: minor releases may add tools
but may not remove, rename, or weaken existing ones. No configuration-schema
change is required.

## Test and evaluation plan

- Service unit tests for one project, multiple projects, deterministic sorting,
  exact output fields, and no runtime initialization.
- MCP schema tests for the tool name, zero required arguments, structured
  output, and read-only annotations.
- Regression tests showing `project_get("keylo")` remains unchanged and an
  invalid alias remains denied.
- Agent evaluation for discovery followed by project inspection.
- Secure-tunnel smoke test from ChatGPT with the prompt: “List the configured
  dbt projects, then inspect the selected project without making changes.”

## Success measures

- 100% pass rate for the new service, schema, and agent-evaluation cases.
- Zero `unknown project` failures in the no-prior-alias agent evaluation.
- A newly connected ChatGPT client can reach a successful `project_get` without
  the user supplying an alias.
- No additional credential, network, filesystem-write, or warehouse activity
  during project discovery.

## Rollout and compatibility

1. Implement and test the MCP tool as an additive change.
2. Document it in the v1 public contract only when the implementation ships.
3. Restart local MCP/tunnel runtimes so clients receive the refreshed catalog.
4. Reconnect or refresh ChatGPT's connector if its cached catalog does not show
   `project_list`.
5. Add CLI parity in the same release if low risk; otherwise ship it as P1.

No migration is required. Existing clients can continue calling `project_get`
with a known alias.

## Decisions and open questions

### Decisions

- The first release lists configured aliases, not discoverable cloud resources.
- The MCP operation has no input parameters.
- The response is intentionally less detailed than `project_get`.
- A dedicated configuration-scoped result keeps `project: null` out of existing
  project-scoped responses.

### Non-blocking open question

- **Engineering:** Should CLI parity ship in the same release or immediately
  after the MCP tool? This does not block the MCP implementation.
