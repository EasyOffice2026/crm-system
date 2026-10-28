# Nexera CRM

A standalone CRM for general businesses: customer records, sales opportunities, follow-ups, quotes, approvals, invoices, customer account ledgers and reporting.

This project has its own codebase and database, independent of management, HRM and accounting-saas.
Its optional built-in Accounting module uses Nexera ERP through server-side APIs.

For the persistent hosted demonstration, see [cloud staging deployment](docs/cloud-staging.md).
This staging setup is not a production SaaS launch.

## Run locally

Requires Python 3.12+ and Node.js 24 with npm. The application uses SQLite by default. Commands below are run from the repository root unless a `cd` is shown.

```sh
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install --upgrade pip==26.2.1
backend/.venv/bin/python -m pip install -e 'backend[dev]'
cd frontend
npm ci
npm run build
cd ../backend
```

Seed fictional demo records, then start the application:

```sh
.venv/bin/python -m app.demo
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Open http://localhost:8001. The API and built React app are served from one origin. API documentation is available at http://localhost:8001/docs.

For live frontend development, leave the API running and start Vite in a second terminal:

```sh
cd frontend
npm run dev -- --port 5173
```

Open http://localhost:5173. Vite proxies `/api` to port 8001.

For a browser preview or reverse proxy that changes the upstream scheme or host, set `CRM_PUBLIC_ORIGIN` to the exact external origin before starting the backend (for example, `https://crm.example.com`, without a path). Browser sign-in and writes must match this origin. When unset, the origin must match the backend request URL. Cross-site writes remain blocked.

### Demo accounts

These accounts and passwords are public development fixtures. Use a separate database for production. Demo databases are refused when `CRM_ENV=production`.

| Role | Email | Password |
| --- | --- | --- |
| Admin | admin@crm.local | CrmDemo!2026 |
| Manager / reviewer | reviewer@crm.local | ReviewDemo!2026 |
| Sales | sales@crm.local | SalesDemo!2026 |
| Finance | finance@crm.local | FinanceDemo!2026 |

Seeding is optional. It does not overwrite a workspace with existing customers or change passwords for existing users. Without seeding, development startup creates `admin@crm.local` using `CRM_ADMIN_PASSWORD` or the development password above. Production startup requires a configured password of at least 12 characters.

## Features

- **Customers:** company and individual records, main and additional contacts, multiple addresses, tax details, tags, payment terms, notes, attachments and activity history; CSV import/export with duplicate-email checks.
- **Sales:** owned opportunities, source, expected closing date, six stages, follow-up planner and overdue indicators.
- **Catalogue:** reusable products/services, units, prices and archiving.
- **Quotes:** line items, quantity, unit price, discounts, tax, validity, terms, revisions, PDF downloads, recorded sending, acceptance and decline.
- **Approvals:** amount and discount rules for quotes; mandatory review for invoices and account transactions; rejection comments, immutable request snapshots and separate document revision history.
- **Invoices:** direct creation or conversion from an accepted quote, due dates, PDFs, partial payment tracking and overdue status.
- **Accounts:** payments with allocation across invoices, unallocated customer credit, credit notes, refunds, first-entry opening balances, positive/negative adjustments, dated running ledgers and PDF/CSV statements.
- **Reports:** net invoiced sales, collections/refunds, current outstanding invoices, five receivables ageing buckets, customer balances, quote conversion and opportunity performance by salesperson; CSV export.
- **Workspace:** company identity, ten supported currencies, correct two/three decimal precision, tax defaults, payment terms, document number prefixes, approval rules, user roles and audit history.

## Financial workflow

### Accounting inside CRM: one company, one ERP organization

The **Accounting** page uses the CRM login and navigation; it reads the linked Nexera ERP
company's chart of accounts, journals and trial balance. Admins, managers and finance users can
access it; sales users cannot. CRM remains a separate application and database, and keeps its
customer subledger. ERP is the general ledger. No shared database or ERP administrator password
is used.

1. Deploy the companion ERP CRM integration API. As an ERP owner/finance manager, call
   `POST /api/v1/companies/{companyId}/crm-integration` using your ERP access token. This creates
   or rotates a credential for **only that legal company in its organization**. Copy the returned
   one-time token into the CRM server's secret configuration as `CRM_ERP_TOKEN`; set `CRM_ERP_URL`
   to the trusted ERP HTTPS origin. Credentials never reach the CRM browser. Never commit them.
2. Both workspaces must already use **KWD with three decimals**. Existing non-KWD records are not
   converted automatically. HTTP loopback is allowed only in non-production local development.
3. Review the ERP receivables balance against the CRM customer balance. Reconcile or explicitly
   match existing financial records before proceeding. Historical transactions are **not imported,
   overwritten or reposted** by this connector.
4. In CRM Accounting, an admin selects the receivables, revenue, receipt/refund clearing, sales
   returns, opening equity and adjustment accounts (and tax payable when applicable), then confirms
   that the existing records have been reconciled. This saves an immutable company/account mapping
   and a future-only ledger cutoff. Concurrent new records require reviewing the cutoff again.
5. Only after reconciliation, set `CRM_ERP_SYNC_ENABLED=true` on CRM and
   `CRM_INTEGRATION_POSTING_ENABLED=true` on ERP. Both are **off by default**. Finance users can
   synchronize up to 10 new customer-ledger transactions per click. This is a manual queue, not
   a background scheduler. Failed entries remain pending; fix the cause and retry.

Issued invoices debit receivables and credit net revenue/tax; receipts debit clearing and credit
receivables. Credits debit sales returns/tax and credit receivables. Partial credit-note tax is
allocated proportionally with cumulative half-up rounding. Refunds reverse clearing against
receivables; opening balances and adjustments use the explicitly selected accounts. Quotes and
draft documents never post. ERP validates account scope, balance, open periods and reconciled-bank
locks. Each event has a stable workspace/ledger ID, duplicate-content protection and a saved ERP
journal ID/number, making retries safe even after a lost response. Successful journal links appear
inside Accounting. Changing a token to another organization/company is rejected after mapping.

Bank reconciliation and full financial-statement authoring inside CRM are not part of this initial
connector; they remain ERP capabilities. A production connection needs the actual organization/company
chosen, credentials provisioned, and historical balances reconciled. Tests use isolated data only.

`Lead → Customer → Quote → Internal approval → Customer acceptance → Invoice → Payment → Ledger / reports`

1. Draft and pending documents do not post to customer accounts.
2. A manager or admin reviews requests. The document author and the requesting user cannot review their own requests. Create a second reviewer before submitting documents from the admin account.
3. Approved invoices and account documents become **issued**, post exactly one ledger entry and cannot be edited or cancelled. Correct invoices using credit notes; correct ledger entries with approved compensating adjustments.
4. Quote revisions archive the preceding revision, return the document to draft and apply the approval rules again. Accepted quotes are frozen. Conversion creates one invoice draft per accepted quote.
5. Amounts are stored as integer minor units, rounded half-up. Documents preserve their currency, precision and customer/company details. Currency becomes fixed after priced records or payments exist.
6. Payment allocation is limited by invoice balances, payment funds and refundable customer credit. A repeated payment request with the same key and content returns the original receipt; reuse for different content is rejected.
7. Credit notes cannot exceed the original invoice total less previous credits. Crediting a paid invoice releases excess allocations back to the payment, with audit records, so that credit can be reallocated or refunded.
8. Refunds consume available payment credit. Opening balances must be the first ledger entry. Ledger balances include account adjustments; invoice ageing reports cover invoice balances only.
9. Report date filters apply to sales and collections. Ageing, outstanding invoices and customer balances are current; these are labelled separately from the selected reporting period.

## Access

| Capability | Admin | Manager | Sales | Finance |
| --- | --- | --- | --- | --- |
| Customer records | Manage | Manage | Manage | Read |
| Opportunities / follow-ups | All | All | Assigned records | Read |
| Quotes | Manage | Manage | Own documents | Read |
| Approvals | Review others | Review others | Own requests | Own requests |
| Invoices / payments / account transactions | Manage | Manage | Read balances | Manage |
| Financial reports | Read | Read | — | Read |
| Catalogue | Manage | Manage | Read | Read |
| Company settings / users | Manage | Read | Read | Read |
| Audit history | Read | Read | Customer history | Customer history |

Sessions last 12 hours and are stored as token hashes. Passwords use scrypt. Cookies are HttpOnly and SameSite Strict, with Secure enabled in production. Writes reject foreign origins; SQLite writes use `BEGIN IMMEDIATE`, while PostgreSQL writes lock the workspace settings row to serialize financial posting.

## Checks

```sh
cd backend
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
.venv/bin/mypy app
.venv/bin/python -m pytest -q
cd ../frontend
npm run lint
npm run typecheck
npm run build
npm audit
```

The API suite exercises the complete quote-to-cash flow, self-approval prevention, immutable invoices, revision snapshots, credit notes on paid invoices, reallocations/refunds, monetary precision, concurrent writes, payment retries, role permissions, cross-origin protection, CSV safety, attachments and date-filtered reports.

## Production configuration

Build the frontend before starting the backend. Use an HTTPS reverse proxy in front of Uvicorn; production session cookies require HTTPS. Set:

| Variable | Purpose |
| --- | --- |
| `CRM_ENV=production` | Production cookie behavior and demo-database protection |
| `CRM_ADMIN_EMAIL` | Initial administrator email for an empty database |
| `CRM_ADMIN_PASSWORD` | Initial administrator password, at least 12 characters |
| `CRM_DATABASE_URL` | SQLAlchemy URL, e.g. `postgresql+psycopg://...` or `sqlite:////persistent/path/crm.db` |
| `CRM_UPLOAD_DIR` | Persistent attachment directory |
| `CRM_PUBLIC_ORIGIN` | External browser origin when a reverse proxy changes the upstream scheme or host |

The bootstrap password is used only when no users exist. Database tables are created on first startup. Configure persistent database/upload storage, limit HTTP request sizes at the proxy and back up both. Use one API worker in this release because login throttling is in process. Never use the demo database for production.

## Hosted workspaces (staging foundation, not enabled in production)

`CRM_TENANCY=hosted` runs one isolated CRM database per client company. A control database
(`CRM_CONTROL_DATABASE_URL`) lists workspaces; each workspace is served only at
`https://<slug>.<CRM_BASE_DOMAIN>`, resolved from the `Host` header that reaches Uvicorn
(`X-Forwarded-Host` is ignored). Unknown or suspended workspaces return 404. Session cookies are
host-only, `Secure`, `HttpOnly`, `SameSite=Strict`, and session tokens carry the workspace id.
Attachments are stored under `CRM_UPLOAD_DIR/<workspace id>/`. Demo data is refused in hosted mode.

Provider operations (never from a workspace browser):

```bash
python -m app.migrate --control                    # create/upgrade the control database
python -m app.provider provision --request-id <uuid> --slug acme --company-name "Acme Trading" \
  --legal-name "Acme Trading Co. W.L.L." --cr-number 123456 --fy-start 2026-01-01 --admin-email owner@acme.com
python -m app.provider_worker                      # continuously finish provider HTTP requests (opt-in below)
python -m app.provider process-pending             # explicit operator retry, including held failures
python -m app.provider rotate-credential acme
python -m app.provider suspend acme
python -m app.provider resume acme                  # verify ERP binding/credential before reopening CRM
python -m app.provider invite acme owner@acme.com  # new one-time admin invite link (72 h)
python -m app.migrate --all-workspaces             # upgrade every active workspace
```

Provisioning is idempotent per request id and resumable: it creates the workspace database and role,
runs migrations, seeds KWD/3-decimal settings, binds an ERP tenant/company, registers an encrypted
per-workspace ERP credential (only its SHA-256 hash is sent to ERP), verifies the connection, enables
accounting with ledger cutoff 0 and creates the first admin with an invite link (or
`CRM_ADMIN_PASSWORD`). The HTTP provider surface only answers on `CRM_PROVIDER_HOST` with
`Authorization: Bearer <key>` checked against `CRM_PROVIDER_API_KEY_SHA256`; it records the request,
and the separate provider worker performs the privileged steps when
`CRM_PROVIDER_WORKER_ENABLED=true` (default false; refuses `CRM_ENV=production`). Run it under a
process supervisor, separately from the unprivileged web process and accounting worker. Only the
provider process gets `CRM_PROVISIONER_DATABASE_URL` and `CRM_ERP_PLATFORM_KEY`. Restrict the provider
host at ingress; do not expose the provider key to client browsers.

The worker checks every five seconds, handles up to 20 workspaces per cycle, and resumes each
idempotent step under a per-workspace PostgreSQL advisory lock. Attempts and retry times survive
restarts in the control database. Transient failures back off from 30 seconds to one hour; conflicts
and eight failed attempts require explicit operator retry. Status includes `attempts`, `nextAttemptAt`
and a sanitized failure. SIGTERM/SIGINT finish the current workspace and stop before the next.
SQLite control databases support only one provider process; use PostgreSQL for multiple workers.
Neither worker logs credentials or invitation URLs. After worker onboarding, issue/deliver the
initial admin link securely using `python -m app.provider invite`; email delivery is not implemented.

Issued invoices, credit notes, refunds and recorded payments write a frozen KWD event to
`crm_accounting_outbox` in the same transaction as the ledger row. `python -m app.accounting_worker`
(`CRM_ACCOUNTING_WORKER_ENABLED=true`, never with `CRM_ENV=production`) posts them to the ERP with the
event id as idempotency key and shows status on documents, payments and **Accounting > Posting queue**.
Opening balances and adjustments cannot be issued in hosted workspaces until balanced automatic
posting for them exists (HTTP 409 with the reason). Existing single-company ledgers are never
replayed: single mode keeps the manual activation/sync path unchanged.

PostgreSQL hosted test (disposable loopback server only; creates a random control database,
limited `CREATEROLE`/`CREATEDB` provisioner and workspace roles/databases, then drops only its own fixtures):
`CRM_TEST_POSTGRES_URL=postgresql+psycopg://<superuser>:<pw>@localhost/<scratch db> .venv/bin/python -m pytest tests/test_hosted_postgres.py`.
A non-superuser provisioner needs `CREATEROLE` and `CREATEDB`; it is granted membership in each
workspace role so it can create that role's database.

Existing single-company databases: run `python -m app.migrate --stamp-existing` once (or just start
the app, which stamps and applies the additive `0002_accounting_outbox` revision).

## Release boundaries

This is a staging foundation, not a production SaaS launch. Browser-driven testing and hosted deployment are separate delivery steps. Workspace database isolation and concurrent accounting workers have been verified against disposable PostgreSQL, alongside SQLite financial tests and real CRM-to-ERP HTTP verification.

The customer account ledger is a subledger; Nexera ERP supplies the general ledger through the server-side integration. Hosted accounting is KWD-only. Opening balances/adjustments, subscription billing, production infrastructure/backups, multi-instance distributed rate limiting and full financial-report/bank-reconciliation authoring inside CRM remain launch gaps. Email/WhatsApp sending, customer self-service acceptance, scheduled email reminders, bilingual screens, uploaded company logos and automated backup scheduling are not implemented. Sending and acceptance actions record communication that happens outside CRM. PDF layouts are English-first. Existing ERP PR dependencies must be reviewed and merged explicitly; this release does not authorize merges or production posting.
