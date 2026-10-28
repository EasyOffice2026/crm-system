# Persistent cloud demonstration

This is a **staging demonstration**, not a commercial production deployment. It uses
separate Fly.io apps and a dedicated PostgreSQL cluster; never point it at an existing
product's database or deploy the ERP staging configuration to the live `nexera-erp` app.
All resources incur recurring Fly.io usage charges.

## Layout

| Resource | Purpose |
| --- | --- |
| `nexera-crm-demo` | CRM frontend/API and supervised accounting worker |
| `nexera-crm-erp-stg` | Separate Nexera ERP accounting service |
| `nexera-crm-stg-db` | Dedicated staging PostgreSQL cluster |
| `crm_uploads` | Persistent, encrypted CRM uploads volume |

The demonstration URL is `https://nexera-crm-demo.fly.dev`. `CRM_BASE_DOMAIN=fly.dev`
and workspace slug `nexera-crm-demo` preserve real Host-based workspace selection and
HTTPS origins without a preview proxy or a custom domain. This is **one reachable demo
workspace**, not wildcard hosting for every client. Commercial multi-client routing
requires an owned domain, wildcard DNS/TLS and the corresponding base-domain setting.
Never use `X-Forwarded-Host` to select a workspace.

CRM control data, each CRM workspace, and ERP data have separate databases and roles.
The database cluster and uploads volume are single-instance staging resources; they
are not highly available. Scheduled volume snapshots do not replace tested database
backups, restore drills or point-in-time recovery.

## Deployment

Install Fly's CLI using its official installer and select the intended organization.
Build from the feature branches containing the hosted connector; do not merge solely
to deploy staging. Review both repository staging configurations before deployment.

Required CRM secrets (stored using `fly secrets import`, never committed):

- `CRM_SECRET_KEY`: generated encryption key, at least 32 characters.
- `CRM_CONTROL_DATABASE_URL`: restricted control-database role.
- `CRM_WORKSPACE_DATABASE_URL`: PostgreSQL host template; each workspace supplies its
  own role, password and database name at runtime.
- `CRM_ERP_URL`: exact HTTPS origin of the separate staging ERP app.

Required ERP secrets: `DATABASE_URL` for its dedicated database, `JWT_ACCESS_SECRET`,
and `PLATFORM_API_KEYS` containing only the provisioning key ID and SHA-256 hash.
The raw ERP platform key and privileged PostgreSQL provisioner credentials must **not**
be installed in the CRM web app's environment. Keep those in the provider secret store
and supply them only to a controlled one-off provisioning process.

Create the PostgreSQL roles/databases and the `crm_uploads` volume first. Revoke PUBLIC
database access. The provider role needs `CREATEDB` and `CREATEROLE`; ordinary CRM/ERP
roles do not. ERP startup applies its own Prisma migrations. CRM's release command
applies control migrations; separately migrate existing workspaces before upgrading
application code that needs workspace schema changes.

From the corresponding repository roots:

```sh
# nexera-erp: NEVER use the live application's fly.toml for this staging service.
fly deploy --config fly.crm-staging.toml --local-only --ha=false

# crm-system: one machine owns the uploads volume; do not scale it horizontally.
fly deploy --config fly.staging.toml --local-only --ha=false
```

The CRM image builds the frontend and runs Uvicorn and the accounting worker under
Supervisor. Both application processes run as the unprivileged `crm` user. The
entrypoint prepares the mounted upload directory. Original Host headers are retained;
CRM does not trust proxy-supplied host/origin headers. Fly terminates HTTPS, and hosted
sessions remain Secure, HttpOnly, host-only and SameSite Strict.

## Access and provisioning

Provision the demo with `python -m app.provider provision` in a controlled provider
process using the separate provisioner credentials and platform key. Its upload root
must be `/data/uploads` on the web machine; run that process as `crm` so directories
remain accessible to the application. Do not store provider credentials on the image
or in shell history. Capture its one-time administrator invitation privately, not in
deployment logs; the user sets their own password using that invitation.

Provider HTTP access and continuous provider provisioning are disabled for this demo.
The accounting worker is enabled only with `CRM_ENV=staging`, operates on the isolated
demo books, and retains its production refusal. No historical records or live-company
balances are imported or replayed. Hosted accounting currently supports KWD only.

Before client use: complete permissions/isolation/failure-path browser checks, choose
the owned SaaS domain, configure managed/high-availability PostgreSQL, external object
storage, monitoring, backup/restore procedures, controlled onboarding, billing and
financial reconciliation. Do not change production-posting gates as part of staging.
