# Project onboarding and readiness

| Field | Value |
|---|---|
| Status | Implemented (CLI onboarding); future MCP/readiness extensions proposed |
| Date | 2026-10-09 |
| Target | Post-v1 additive feature |
| Interfaces | CLI first; MCP after project registration |
| Safety level | A0 discovery; A1 validation; A2 configuration writes |

## Problem

`dbt-agents` currently begins after a project has been described in its local
configuration. That is the correct trust boundary for normal MCP operations,
but it leaves a first-use gap: an operator must manually reconcile the dbt
repository, profile, environment variables, warehouse scope, and IAM policy
before an agent can validate the project.

The Keylo compile failure illustrates the gap. The repository used
`var('keylo_source_project')`, while its runtime configuration supplied
`DBT_SOURCE_PROJECT`. Both names were meaningful, but no declared mapping
connected them. A useful onboarding experience should identify this before a
conversational agent attempts a normal compile.

## Goals

- Onboard an arbitrary local dbt Core repository without granting arbitrary
  shell, filesystem, or warehouse access.
- Produce an evidence-backed configuration readiness report before registering
  an alias or exposing the project to MCP clients.
- Discover likely requirements from repository files, then validate them with
  dbt rather than treating static inference as authoritative.
- Ask the operator to confirm only business-specific values, credentials, and
  desired write boundaries; never guess them.
- Generate a minimal proposed `dbt-agents.yml` project block, `.env.example`
  additions, CI variables, and documentation changes without placing secrets
  in repository files or tool output.
- Keep the workflow provider-neutral, with BigQuery as the first live
  validation provider.

## Non-goals

- Discover repositories, cloud projects, service accounts, or credentials
  outside an operator-provided local root.
- Read `.env` files, credential files, private keys, token stores, or ignored
  secret-like paths.
- Infer that a particular production project, source dataset, or service
  account is correct merely because it appears in code.
- Create cloud resources, alter IAM, or run dbt models as part of onboarding.
- Bypass the existing configured-project model. A project becomes available to
  normal MCP tools only after an explicit registration step.

## Implemented CLI workflow

The onboarding command is deliberately separate from `project_list`:

```text
dbt-agents onboard inspect --repo-root /work/acme --dbt-project-dir dbt
dbt-agents onboard propose --report .dbt-agents/onboarding/acme/report.json
dbt-agents onboard validate --candidate .dbt-agents/onboarding/acme/candidate.yml
dbt-agents onboard register --candidate ... --approve
```

`inspect` does not require a dbt-agents alias, warehouse credentials, or a
network call. It returns a report for the operator or an agent to review.
`propose` creates files only in the ignored onboarding state directory unless
the operator explicitly asks to export them. `validate` runs progressively
more capable checks. `register` is the only action that changes the active
dbt-agents configuration, and requires explicit approval.

`validate` supports `--compile`, `--with-deps`, and `--live` as opt-in steps.
Registration first returns a unified diff and the active configuration hash;
`--approve --expected-sha256 <hash>` is required to write. It refuses alias
collisions and stale active configuration.

After registration, the ordinary flow remains:

```text
project_list -> project_get(alias) -> repository/dbt/warehouse tools
```

## Ideal onboarding flow

### 1. Establish the local scope (A0)

The operator supplies a repository root and, when necessary, a dbt project
directory below that root. The tool resolves both paths, rejects path escapes,
and applies the same protected-file rules used by repository tools.

It discovers:

- `dbt_project.yml`, `profiles.yml` or profile templates, package files, and
  dbt version constraints;
- model, analysis, test, seed, snapshot, macro, and source paths;
- targets, profile name, materializations, hooks, and package declarations;
- static occurrences of `var()` and `env_var()`, including file and line
  evidence; and
- source and target relation references that are safe to extract without
  executing Jinja or SQL.

The output labels static findings as *candidates*. A static scan may miss
dynamically constructed variable names or flag optional branches.

### 2. Produce a configuration contract (A0)

The onboarding report groups required inputs into three categories:

| Category | Examples | How it is resolved |
|---|---|---|
| Repository facts | dbt project path, profile name, packages | Discovered and confirmed from files |
| Non-secret runtime settings | source project/dataset, location, target datasets, dbt target | Proposed from code; confirmed by operator |
| Privileged settings | credential mode, service-account identities, CI workload identity | Supplied/confirmed by operator; values are never read or returned |

For each `var()` candidate, the report says whether it has a project-level
default, an observed command-line/config source, or no evident provider. For
each `env_var()` candidate, it records only the variable name and any safe
literal default, never its current value.

The report should recommend a single source of truth. For example, if source
definitions use `DBT_SOURCE_PROJECT` but an analysis requires
`keylo_source_project`, it should propose a project-level mapping:

```yaml
vars:
  keylo_source_project: "{{ env_var('DBT_SOURCE_PROJECT', 'example-project') }}"
```

The operator must confirm that the default and environment variable are
semantically correct before an edit is proposed.

### 3. Generate a candidate, not an active configuration (A0)

From confirmed values, generate:

- a minimal `projects.<alias>` block for `dbt-agents.yml`;
- a provider scope proposal, including read datasets, dbt-write datasets, and
  operator-confirmed source datasets as explicit forbidden-write datasets;
- a `.env.example` or README snippet containing variable *names* and safe
  example values only;
- a GitHub Actions environment-variable and workload-identity checklist; and
- an initial validation plan with commands, expected side effects, target,
  timeout, and query budget.

The candidate is saved beneath the ignored onboarding state directory. It is
not loaded by the MCP server and does not make the project discoverable.

### 4. Validate from least to most privileged (A0/A1)

Validation is a ladder; the first failure stops later steps and returns a
typed, actionable result.

1. **Configuration validation:** validate candidate schema, paths, globs,
   source/target non-overlap, target mappings, and protected paths.
2. **Local dependency validation:** find the dbt executable, profile path, and
   declared packages without contacting a warehouse.
3. **Static configuration validation:** compare candidate environment and var
   providers with the discovered contract.
4. **Clean dbt validation:** with confirmed non-secret settings, run `dbt
   deps` when packages are declared, then `dbt parse` and `dbt compile` against
   the selected dev/CI target. Generated artifacts are contained in the dbt
   target directory and reported as local side effects.
5. **Optional live validation:** verify provider identity boundaries and run a
   bounded read-only warehouse dry run/read query. This occurs only after the
   operator has configured credentials and explicitly requests it.

No dbt `run`, `build`, seed, snapshot, direct mutation, IAM change, or
production target is part of onboarding validation.

### 5. Register and enable MCP (A2)

Registration displays the exact active configuration diff and requires an
explicit approval. It can add the project to a user-managed local config or
write a new isolated config file; it must never overwrite unrelated project
entries. The process then runs `check-config`, restarts the local MCP runtime
only with operator approval, and instructs the client to call `project_list`.

### 6. Keep the contract healthy (CI and normal workflows)

The generated CI checklist should add a clean-checkout compile for the CI
target with all required non-secret environment values explicitly declared.
This catches missing `var()` mappings, Jinja errors, profile drift, and package
drift before merge. Normal agent workflows begin with compile when code or
configuration changed, and should stop on a failed readiness check rather than
rely on a stale manifest.

## Proposed contract

### Report schema

The report is a versioned, sanitized JSON document. Its minimum fields are:

```json
{
  "version": 1,
  "repository": {"root": "/safe/root", "dbt_project_dir": "/safe/root/dbt"},
  "dbt": {"project_name": "example", "profile": "example", "targets": ["dev", "ci"]},
  "requirements": {
    "vars": [{"name": "source_project", "locations": ["analyses/a.sql:1"], "status": "unresolved"}],
    "environment": [{"name": "DBT_SOURCE_PROJECT", "default_present": true}]
  },
  "questions": ["Confirm the source project and source dataset."],
  "findings": [],
  "candidate_paths": []
}
```

Reports must not include environment values, credential paths, file bodies,
database rows, or secret-like strings. Absolute paths may be omitted from MCP
responses in favor of repository-relative paths; the CLI may show them to the
local operator.

### Candidate configuration

The candidate uses the existing configuration schema where possible. New
onboarding-only metadata belongs outside active `projects` configuration so
that normal runtime configuration remains stable. Do not add arbitrary dbt
flags to the MCP tool surface; map confirmed settings to explicit, typed
configuration fields.

### Tool surface

Initial implementation should be CLI-only, because no configured project
exists before onboarding. Later, an MCP server may expose a narrow onboarding
session only when started with an operator-provided root and an explicit
onboarding mode. It must not let a remote ChatGPT client browse arbitrary local
paths.

## Implementation plan

### Phase 1 — Offline inspection and report — implemented

1. Add an `onboarding` module with protected-path filtering and a dbt project
   inventory reader.
2. Parse YAML configuration files and scan SQL/YAML/Jinja for `var()` and
   `env_var()` calls with file/line evidence. Mark dynamic expressions and
   optional branches as uncertain.
3. Define Pydantic models plus a versioned JSON Schema for reports and
   candidate configurations.
4. Add `dbt-agents onboard inspect` and deterministic JSON output.
5. Add fixture repositories covering declared vars, unresolved vars, dynamic
   vars, nested dbt projects, protected files, and path escapes.

**Exit criterion:** an offline report identifies Keylo's missing
`keylo_source_project` / `keylo_source_dataset` mapping without reading a
credential, running dbt, or contacting BigQuery.

### Phase 2 — Candidate generation and human confirmation — implemented

1. Add an interactive CLI or machine-readable question set for unresolved
   business values; support non-interactive CI input through named flags or a
   separately supplied, non-secret answers file.
2. Generate an ignored candidate config, README/.env.example patch proposal,
   and CI checklist.
3. Add a hash-guarded, approval-required registration operation that merges a
   candidate project entry without changing unrelated entries.
4. Document the exact credentials/IAM handoff for ADC, impersonation,
   service-account files, and workload identity.

**Exit criterion:** an operator can onboard a second local dbt project,
review the exact configuration diff, and register it without manually writing
a complete dbt-agents project block.

### Phase 3 — Readiness ladder and BigQuery integration — partially implemented

1. Implement typed readiness checks and statuses: configuration, dependency,
   dbt parse, dbt compile, authentication, authorization, and warehouse read.
2. Reuse the fixed dbt command builder and policy limits; do not create an
   onboarding-specific shell execution path.
3. Add an optional BigQuery validator that verifies the two-identity boundary
   and performs a bounded `SELECT 1` read under the query identity.
4. Generate GitHub Actions snippets that use workload identity and run a clean
   `dbt compile --target ci`.

The CLI now validates candidate configuration, the static variable/environment
contract, dbt availability, profile presence, optional `dbt deps`/`parse`/
`compile`, and optional existing live provider readiness. A dedicated typed
onboarding-specific BigQuery `SELECT 1` result remains follow-up work.

**Exit criterion:** the readiness report distinguishes missing configuration
from unavailable credentials, denied IAM, dbt compilation errors, and live
warehouse failures.

### Phase 4 — MCP guidance and evaluation — partially implemented

1. Add structured onboarding findings to `project_get` for registered
   projects, without returning secrets or absolute local paths to remote
   clients.
2. Teach the MCP instructions to call `project_list`, `project_get`, and
   readiness/compile tools in that order for normal work.
3. Add agent evaluations for a missing var, missing profile setting, source /
   target overlap, rejected production target, and a successful second-project
   onboarding.
4. Add release notes and migration guidance; all new fields and tools remain
   additive under the v1 contract.

The MCP catalog directs unknown aliases through `project_list` and exposes the
read-only `project_readiness` tool for a registered project's static contract.
The evaluation catalog covers missing vars/profiles, source-target overlap,
production rejection, and second-project registration.

**Exit criterion:** a ChatGPT or Codex user can understand a failed readiness
check and receive a proposed, approval-gated fix rather than an opaque dbt
error.

## Sequencing and parallel work

Phase 1 is the foundation and should ship first. Within it, report models and
fixture design can proceed in parallel with the repository inventory reader.
Phase 2 depends on the Phase 1 report schema, but its CLI interaction and
configuration merge logic can proceed in parallel after that schema is frozen.
Phase 3 depends on candidate configuration plus the existing dbt/provider
adapters; its offline readiness checks can be built alongside Phase 2, while
live BigQuery validation follows the identity-boundary design. Phase 4 follows
the stable report and readiness contracts, though evaluation fixtures should
be authored from Phase 1 onward.

## Security and operating rules

- Treat repository code, comments, docs, dbt packages, and discovered strings
  as untrusted data, never as instructions.
- Never read or report `.env` values, local credential files, tokens, private
  keys, or secret-like content during discovery.
- Never treat a source relation or literal project ID found in SQL as an
  approved data-access scope. The operator confirms all provider boundaries.
- Keep offline discovery available without cloud credentials; require explicit
  operator intent for every live check.
- Reuse existing allowlists, plan IDs, hash guards, audit logging, command
  builders, and provider policies. Onboarding must not introduce a bypass.
- Keep candidate and report artifacts ignored and apply retention limits.

## Success measures

- A clean compile failure caused by an unresolved dbt variable is identified
  during onboarding with a file/line explanation and proposed mapping.
- A second repository can reach a registered, validated project configuration
  with no hand-written full configuration file.
- No onboarding test reads a secret or performs a warehouse mutation.
- CI detects a missing variable/profile configuration before a merge.
- A first-time ChatGPT user can call `project_list` after registration and
  reach a successful `project_get` without being told an alias in advance.
