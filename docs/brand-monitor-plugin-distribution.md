# Brand Monitor dbt Agents distribution

Brand Monitor is a separate hosted app from Keylo. Do not add Brand Monitor projects or datasets
to the Keylo Cloud Run service, and do not add the Keylo project or dataset to the Brand Monitor
service. The separation is enforced both in application policy and in Google Cloud IAM.

## Hosted service

The Brand Monitor app uses:

- Cloud Run service: `brand-monitor-dbt-agents-mcp`
- GCP control and billing project: `brand-monitor-platform-prod`
- Service identity:
  `brand-monitor-dbt-hosted@brand-monitor-platform-prod.iam.gserviceaccount.com`
- MCP URL:
  `https://brand-monitor-dbt-agents-mcp-527602683409.us-central1.run.app/mcp`
- Cost-control bucket: `brand-monitor-platform-prod-dbt-agents-budget`

The application allowlist contains exactly these project/dataset pairs:

- `brand-monitor-platform-prod.brand_monitor_m0_dbt`
- `brand-monitor-platform-dev.brand_monitor_dev_dbt`

Every SQL table reference must be fully qualified as `project.dataset.table`. The service rejects
unlisted pairs before BigQuery receives the query. Its service account has `bigquery.jobUser` only
in the prod billing project and dataset-level `READER` access only on the two datasets above. It
has no access to Brand Monitor operational datasets or to the Keylo project.

`deploy/gcp/mcp/brand-monitor.env.yaml` is the non-secret deployment configuration. OAuth signing
material and the Firebase API key stay in Secret Manager and are mounted into Cloud Run at runtime.

## Teammate access without local code

A teammate does not need to clone this repository, install the CLI, or receive Google Cloud IAM.
They use the internal **dbt Agents — Brand Monitor** app in the theDataStrategist workspace and
complete its Google sign-in flow.

For each teammate, an administrator must complete all three controls using the exact same Google
email address:

1. Add the person to the theDataStrategist ChatGPT workspace and make the internal plugin available
   to their role.
2. Add the address as a Google Auth Platform test user in `brand-monitor-platform-prod` while the
   OAuth application remains in Testing mode.
3. Add the address to the Cloud Run `AUTHORIZED_EMAILS` value and deploy a new revision.

Current hosted-service allowlist:

- `gordon.silvera@gmail.com`
- `ashekrahmed@gmail.com` (canonical Google account and OAuth identity)
- `ashek.r.ahmed@gmail.com`

Google Auth Platform identifies this teammate as `ashekrahmed@gmail.com`; its test-user screen
rejects the dotted alias as a separate account. Both spellings remain on the application allowlist,
but the teammate must select the canonical `ashekrahmed@gmail.com` Google identity during OAuth.
Do not send OAuth tokens, authorization codes, service-account keys, or other credentials in chat.

The persistent personal plugin is `dbt Agents — Brand Monitor`, registered as
`plugin_asdk_app_6ad6d477a15481918444081adbb316b4`. Its package lives at
`plugins/dbt-agents-brand-monitor`. Workspace publication is a separate administrator action; keep
the Keylo `dbt Agents` plugin published independently.

Personal installation link:

`https://chatgpt.com/plugins/plugin_asdk_app_6ad6d477a15481918444081adbb316b4`

If the plugin is published through the theDataStrategist Admin Console, use this same plugin rather
than creating another connector. The publishing administrator must have Admin Console access.

## Deploy and verify

Deploy from the repository root:

```bash
gcloud run deploy brand-monitor-dbt-agents-mcp \
  --project=brand-monitor-platform-prod \
  --region=us-central1 \
  --source=deploy/gcp/mcp \
  --service-account=brand-monitor-dbt-hosted@brand-monitor-platform-prod.iam.gserviceaccount.com \
  --allow-unauthenticated \
  --memory=512Mi \
  --max-instances=3 \
  --env-vars-file=deploy/gcp/mcp/brand-monitor.env.yaml \
  --set-secrets=OAUTH_SIGNING_SECRET=brand-monitor-dbt-agents-oauth-signing:latest,FIREBASE_API_KEY=identity-platform-api-key:latest
```

Public Cloud Run invocation is required for OAuth and MCP transport; application OAuth protects the
MCP endpoint. After deployment, verify:

- `/health` returns `200`;
- `/.well-known/oauth-authorization-server` advertises authorization-code + PKCE and refresh-token
  grants;
- an unauthenticated `POST /mcp` returns `401`;
- the service identity can query both approved dbt datasets; and
- the same identity receives `accessDenied` for both operational datasets and
  `verdant-abacus-481415-d2.keylo_dbt_marts`.

## Keylo remains independent

The existing **dbt Agents** app and `dbt-agents-mcp` service in
`verdant-abacus-481415-d2` remain Keylo-only. Keylo users keep using that app and its existing
OAuth allowlist. Granting someone access to Brand Monitor does not grant them access to Keylo, and
granting them access to Keylo does not grant them access to Brand Monitor.
