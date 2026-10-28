# Hosted CRM ↔ nexera-erp real HTTP integration test

`backend/tests/test_hosted_erp_http.py` runs a real CRM API server and a real nexera-erp API server and checks
hosted onboarding, automatic accounting posting and cross-client isolation over HTTP. It is opt-in: without the
two variables below it is skipped, so the normal `pytest -q` run is unchanged.

## Safety

- Everything binds and connects to loopback only. The PostgreSQL URL must point to `localhost`, `127.0.0.1` or
  `::1`; anything else is refused. The CRM and ERP servers listen on random `127.0.0.1` ports.
- No production settings or credentials are read. The servers get a minimal environment (`PATH`, `HOME`,
  `LANG`, `TZ`) plus generated values: a random CRM secret key, a random ERP JWT secret, a random `nxp_` platform
  key, and a random admin password. A local `nexera-erp/.env`, if present, only fills variables not set here.
- Each run uses a random id for database names (`nexera_e2e_<id>`, `crm_e2e_ctl_<id>`), workspace slugs
  (`e2ea<id>`, `e2eb<id>`), customer names and references.
- Cleanup always runs, even after a failure. It stops both servers, then drops only what that run created: its ERP
  and control databases, plus the `crm_ws_*` databases and roles recorded in its own control database for its
  own slugs. Set `CRM_E2E_KEEP=1` to keep them for debugging.
- ERP posting and provisioning flags are enabled only in the throwaway ERP process started by the test.

## Prerequisites

- A disposable local PostgreSQL 16+ server with a superuser login. For example, the nexera-erp
  `docker compose up -d` database (`nexera:nexera@127.0.0.1:5432`).
- A nexera-erp checkout with `npm ci` already run, and Node from its `.nvmrc` on `PATH`.
- The CRM backend virtualenv (`cd backend && python3.12 -m venv .venv && .venv/bin/pip install -e .`).

## Run

```bash
cd backend
CRM_E2E_POSTGRES_URL=postgresql://nexera:nexera@127.0.0.1:5432/postgres \
CRM_E2E_ERP_DIR=/path/to/nexera-erp \
.venv/bin/python -m pytest -q -s tests/test_hosted_erp_http.py
```

`-s` prints one `PASS`/`FAIL` line per check, followed by a `{"passed": N, "failed": M}` summary. The test fails if
any check fails. Expect 62 checks.

## Coverage

- Onboarding: provider HTTP requests queue two clients, the separate provider worker provisions them through
  the ERP platform API, and one-time admin invitations are delivered/accepted. Each gets its own ERP
  tenant and company, the `general_business_v1` chart, a posting profile, 12 open periods, no human ERP users,
  and one hash-only service credential.
- Non-posting activity creates no outbox rows: the quote lifecycle, a draft invoice, a task and a won opportunity.
- An issued invoice, a recorded payment, an issued credit note and an issued refund each create one outbox event.
  The worker posts each as a balanced ERP journal in the correct company with the expected lines, and the
  customer subledger party and balance are checked.
- Duplicates: a retry after a lost response re-links the same journal, a direct replay returns
  `duplicate: true`, and a changed payload with the same event id returns `409 CRM_EVENT_CONFLICT`.
- Isolation:
  - Cookies are rejected on the other host, including with a spoofed `X-Forwarded-Host`.
  - Users cannot sign in to the other workspace.
  - Neither client can read the other's CRM data.
  - The other client's ERP token cannot read or post into the first client's company.
  - Service tokens are refused on legacy routes and on the platform API.
