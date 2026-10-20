# dbt Agents plugin distribution

The supported distribution path is currently **internal**. The Keylo package wraps the registered
remote dbt Agents MCP connection and gives it stable product metadata, a guided first-run skill,
and a repository marketplace entry. Brand Monitor is intentionally a separate app and service;
see [Brand Monitor plugin distribution](brand-monitor-plugin-distribution.md).

## Install from this checkout

From the repository root:

```bash
make plugin-install
```

Then restart the ChatGPT desktop app, open the Plugins Directory, choose **The Data Strategist
Internal**, and install **dbt Agents**. When prompted, connect with an approved Google account.
The hosted service requires both workspace access and membership in its internal OAuth allowlist.
It does not accept credentials or service-account keys from chat.

The equivalent manual commands are:

```bash
codex plugin marketplace add /absolute/path/to/dbt-agents
codex plugin add dbt-agents@thedatastrategist-internal
```

Validate the package without installing it:

```bash
make plugin-check
```

## Team distribution

After these files are merged, teammates can add the Git repository as their marketplace source:

```bash
codex plugin marketplace add the-datastrategist/dbt-agents --ref main
codex plugin add dbt-agents@thedatastrategist-internal
```

The registered MCP app uses per-user Google OAuth. The Google Auth Platform app stays in
**External / Testing** mode, and every teammate must be listed as both an OAuth test user and in
the Cloud Run service's `AUTHORIZED_EMAILS` setting. Do not put access tokens, OAuth client
secrets, service-account keys, or reviewer credentials in this repository. Workspace admins must
also make the required app available to intended roles; the app reference alone does not grant
access.

Current internal test users:

- `gordon.silvera@gmail.com`
- `giacomohasbani14@gmail.com`

The hosted service runs in Google Cloud project `verdant-abacus-481415-d2` at
`https://verdant-abacus-481415-d2.web.app/mcp`. Firebase Hosting provides the stable public URL
and proxies requests to the `dbt-agents-mcp` Cloud Run service. The service exposes only the
`keylo_dbt_marts` dataset and uses its own query identity and bounded read-only policy. To add a
teammate, update both the Google Auth Platform test-user list and the comma-delimited
`AUTHORIZED_EMAILS` Cloud Run environment value, then ask that teammate to install and connect
the plugin with the same Google address.

Do not reuse this Keylo connection for Brand Monitor. The Brand Monitor service has a different
identity, endpoint, OAuth allowlist, and exact `project.dataset` allowlist so neither product can
read the other's warehouse data.

## Public submission decision

Do not upload the internal package to the public submission portal. It intentionally uses
`.app.json` to reference an existing registered connection; public remote-MCP submissions must
declare and review the hosted endpoint instead.

A future public package would need to:

1. Replace the registered app reference with a root `mcp.json` describing the stable HTTPS MCP
   endpoint and OAuth flow.
2. Remove the internal email allowlist and implement an appropriate tenant and entitlement model.
3. Publish product, support, privacy-policy, and terms-of-service pages on an owned domain.
4. Complete OpenAI developer verification, domain verification, review cases, and the walkthrough.
5. Obtain a positive eligibility determination for dbt Agents as an original, policy-enforced dbt
   analytics product rather than an unofficial BigQuery pass-through connector.

Until those conditions are met, workspace or repository distribution is the simpler and safer
release channel.
