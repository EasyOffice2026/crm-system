# Nexera CRM

A standalone CRM for general businesses: customer records, sales opportunities, follow-ups, quotes, approvals, invoices, customer account ledgers and reporting.

This project has its own codebase and database. It does not connect to the management, HRM or accounting applications.

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

The bootstrap password is used only when no users exist. Database tables are created on first startup. Configure persistent database/upload storage, limit HTTP request sizes at the proxy and back up both. Use one API worker in this release because login throttling is in process. Never use the demo database for production.

## Release boundaries

This is an initial working implementation. Browser-driven testing and hosted deployment are separate delivery steps. The automated financial tests currently run against SQLite; PostgreSQL compatibility is implemented but has not yet been validated against a live PostgreSQL server.

The customer account ledger is a customer subledger, not a full general ledger. Email/WhatsApp sending, customer self-service acceptance, scheduled email reminders, bilingual screens, external accounting integrations, uploaded company logos, schema migrations for later upgrades and automated backup scheduling are not implemented in this release. Sending and acceptance actions record communication that happens outside the CRM. PDF layouts are English-first.
