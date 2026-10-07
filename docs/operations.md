# Operational readiness

## Readiness checks

Run offline checks first:

```bash
dbt-agents doctor keylo --config dbt-agents.yml
```

Then opt into live, read-only BigQuery checks:

```bash
dbt-agents doctor keylo --live --config dbt-agents.yml
```

Live mode fetches metadata for every configured readable dataset and runs the
bounded `select 1 as ok` query through the normal query policy. It does not run
dbt or write warehouse data.

## IAM boundary

IAM planning and verification require `auth.mode: impersonation` and distinct
query/dbt service-account environment values:

```bash
dbt-agents iam-plan keylo --config dbt-agents.yml
dbt-agents iam-verify keylo --config dbt-agents.yml
```

`iam-plan` prints the required bindings without changing cloud state.
`iam-verify` reads project IAM and BigQuery dataset access entries. It confirms
both identities have job execution, rejects broad project-level BigQuery data
roles, requires the query identity to be a dataset reader, and requires the dbt
identity to be a reader on sources and writer on dbt targets.

Verification deliberately does not attempt a forbidden write: if IAM is
misconfigured, a negative write test could succeed and create unwanted data.

For a new project, pass the reviewed service-account emails explicitly. Applying
the plan is a separate, approval-gated command and never creates keys:

```bash
dbt-agents iam-plan keylo \
  --query-identity keylo-dbt-query@PROJECT.iam.gserviceaccount.com \
  --dbt-identity keylo-dbt-runner@PROJECT.iam.gserviceaccount.com

dbt-agents iam-apply keylo \
  --query-identity keylo-dbt-query@PROJECT.iam.gserviceaccount.com \
  --dbt-identity keylo-dbt-runner@PROJECT.iam.gserviceaccount.com \
  --approve
```

`iam-apply` is idempotent: it creates missing service accounts, grants only
project-level `roles/bigquery.jobUser`, and replaces those identities' dataset
entries with the planned READER/WRITER roles while preserving other grants.

## Change validation

Compile the current change:

```bash
dbt-agents validate keylo --selector state:modified+ --target ci \
  --config dbt-agents.yml
```

Include targeted dbt tests only after reviewing the resolved target and adding
the explicit approval flag:

```bash
dbt-agents validate keylo --selector my_model+ --target ci \
  --with-tests --approve --config dbt-agents.yml
```

Production remains a separate A3 operation. This workflow does not push, open a
pull request, or execute a production target.

## Release

The release workflow builds and tests tagged revisions, then uses PyPI trusted
publishing from the protected `pypi` GitHub environment. Configure that
environment and the PyPI trusted publisher before pushing a `v*` tag.
