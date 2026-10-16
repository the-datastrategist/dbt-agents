# dbt Agents plugin distribution

The supported distribution path is currently **internal**. The package wraps the registered
remote dbt Agents MCP connection and gives it stable product metadata, a guided first-run skill,
and a repository marketplace entry.

## Install from this checkout

From the repository root:

```bash
make plugin-install
```

Then restart the ChatGPT desktop app, open the Plugins Directory, choose **The Data Strategist
Internal**, and install **dbt Agents**. The required app currently uses the hosted service's
configured identity rather than a per-user sign-in, so workspace role access and configured
project boundaries determine what a teammate can use.

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

The registered MCP app currently uses no per-user authorization. Do not put access tokens, OAuth
client secrets, service-account keys, or reviewer credentials in this repository. Workspace admins
must make the required app available to intended roles; the app reference does not grant access.

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
   workflow product rather than an unofficial BigQuery pass-through connector.

Until those conditions are met, workspace or repository distribution is the simpler and safer
release channel.
