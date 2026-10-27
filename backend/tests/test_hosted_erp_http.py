"""Opt-in real HTTP test of hosted CRM workspaces against a real nexera-erp API.

Everything runs on loopback with throwaway databases and random names; see docs/hosted-erp-http-test.md.
"""

import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
import pytest
from psycopg import sql

POSTGRES = os.environ.get("CRM_E2E_POSTGRES_URL", "")
ERP_DIR = os.environ.get("CRM_E2E_ERP_DIR", "")
KEEP = os.environ.get("CRM_E2E_KEEP") == "1"
BACKEND = Path(__file__).resolve().parents[1]
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
BASE_DOMAIN = "crm.localtest"
YEAR = date.today().year

pytestmark = pytest.mark.skipif(
    not (POSTGRES and ERP_DIR),
    reason="Set CRM_E2E_POSTGRES_URL (loopback superuser DSN) and CRM_E2E_ERP_DIR (nexera-erp checkout)",
)

ERP_LOOPBACK_SERVER = """
import {{ createApp }} from {app};
import {{ loadConfig }} from {config};
import {{ createPrismaClient }} from {prisma};

const config = loadConfig();
const prisma = createPrismaClient(config.databaseUrl);
const server = createApp({{ prisma, config }}).listen(config.port, '127.0.0.1', () => console.log('ready'));
process.on('SIGTERM', () => server.close(() => void prisma.$disconnect().finally(() => process.exit(0))));
"""


def db_url(name: str, driver: str = "postgresql") -> str:
    parts = urlsplit(POSTGRES)
    return urlunsplit((driver, parts.netloc, "/" + name, "", ""))


def admin_db() -> str:
    return urlsplit(POSTGRES).path.lstrip("/") or "postgres"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_http(url: str, process: subprocess.Popen[bytes], log: Path) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{url} server exited:\n{log.read_text()[-3000:]}")
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not become ready:\n{log.read_text()[-3000:]}")


@dataclass
class Stack:
    run: str
    erp_db: str
    control_db: str
    erp_url: str
    crm_url: str
    crm_env: dict[str, str]
    admin_password: str
    slugs: dict[str, str]
    processes: list[subprocess.Popen[bytes]] = field(default_factory=list)


def check_safe_config() -> None:
    parts = urlsplit(POSTGRES)
    if parts.scheme not in {"postgresql", "postgres"} or parts.hostname not in LOOPBACK:
        raise RuntimeError("CRM_E2E_POSTGRES_URL must be a postgresql:// URL on localhost/127.0.0.1/::1")
    if not (Path(ERP_DIR) / "apps" / "api" / "src" / "app.ts").is_file():
        raise RuntimeError("CRM_E2E_ERP_DIR must point to a nexera-erp checkout with dependencies installed")


def admin_execute(*statements: sql.Composable) -> None:
    with psycopg.connect(db_url(admin_db()), autocommit=True) as connection:
        for statement in statements:
            connection.execute(statement)


def cleanup(stack: Stack) -> None:
    for process in stack.processes:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(15)
            except subprocess.TimeoutExpired:
                process.kill()
    if KEEP:
        print(
            f"CRM_E2E_KEEP=1: kept databases {stack.erp_db}, {stack.control_db} and crm_ws_* of run {stack.run}"
        )
        return
    workspaces: list[tuple[str, str]] = []
    try:
        with psycopg.connect(db_url(stack.control_db)) as connection:
            workspaces = [
                tuple(row) for row in connection.execute("SELECT db_name, db_role FROM crm_workspaces")
            ]
    except psycopg.Error:
        pass
    slugs = set(stack.slugs.values())
    for name, role in workspaces:
        if name.startswith("crm_ws_") and role.startswith("crm_ws_") and any(slug in name for slug in slugs):
            admin_execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)),
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)),
            )
    for name in (stack.erp_db, stack.control_db):
        admin_execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    check_safe_config()
    run = secrets.token_hex(4)
    erp_port, crm_port = free_port(), free_port()
    key_id = "e2e" + run
    platform_key = f"nxp_{key_id}_{secrets.token_urlsafe(32)}"
    base_env = {name: os.environ[name] for name in ("PATH", "HOME", "LANG", "TZ") if name in os.environ}
    current = Stack(
        run=run,
        erp_db=f"nexera_e2e_{run}",
        control_db=f"crm_e2e_ctl_{run}",
        erp_url=f"http://127.0.0.1:{erp_port}",
        crm_url=f"http://127.0.0.1:{crm_port}",
        crm_env={},
        admin_password=f"E2e-{secrets.token_urlsafe(12)}-9a!",
        slugs={"alpha": f"e2ea{run}", "beta": f"e2eb{run}"},
    )
    try:
        admin_execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(current.erp_db)),
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(current.control_db)),
        )
        api = Path(ERP_DIR).resolve() / "apps" / "api"
        erp_env = {
            **base_env,
            "NODE_ENV": "development",
            "API_PORT": str(erp_port),
            "DATABASE_URL": db_url(current.erp_db),
            "PASSWORD_HASH_PROFILE": "fast",
            "JWT_ACCESS_SECRET": secrets.token_urlsafe(48),
            "PLATFORM_PROVISIONING_ENABLED": "true",
            "PLATFORM_API_KEYS": f"{key_id}:{hashlib.sha256(platform_key.encode()).hexdigest()}",
            "CRM_INTEGRATION_POSTING_ENABLED": "true",
        }
        prisma = Path(ERP_DIR).resolve() / "node_modules" / ".bin" / "prisma"
        migrated = subprocess.run(
            [str(prisma), "migrate", "deploy"], cwd=api, env=erp_env, capture_output=True, text=True
        )
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        server = tmp_path / "erp-loopback-server.ts"
        server.write_text(
            ERP_LOOPBACK_SERVER.format(
                **{
                    name: json.dumps(str(api / "src" / path))
                    for name, path in (
                        ("app", "app.ts"),
                        ("config", "config.ts"),
                        ("prisma", "lib/prisma.ts"),
                    )
                }
            )
        )
        erp_log = tmp_path / "erp.log"
        current.processes.append(
            subprocess.Popen(
                ["node", "--import", "tsx", str(server)],
                cwd=api,
                env=erp_env,
                stdout=erp_log.open("wb"),
                stderr=subprocess.STDOUT,
            )
        )
        wait_http(f"{current.erp_url}/api/v1/health", current.processes[-1], erp_log)
        current.crm_env = {
            **base_env,
            "CRM_ENV": "development",
            "CRM_TENANCY": "hosted",
            "CRM_CONTROL_DATABASE_URL": db_url(current.control_db, "postgresql+psycopg"),
            "CRM_WORKSPACE_DATABASE_URL": db_url(admin_db(), "postgresql+psycopg"),
            "CRM_PROVISIONER_DATABASE_URL": db_url(admin_db(), "postgresql+psycopg"),
            "CRM_BASE_DOMAIN": BASE_DOMAIN,
            "CRM_WORKSPACE_SCHEME": "http",
            "CRM_PROVIDER_HOST": f"provider.{BASE_DOMAIN}",
            "CRM_SECRET_KEY": secrets.token_urlsafe(48),
            "CRM_UPLOAD_DIR": str(tmp_path / "uploads"),
            "CRM_ERP_URL": current.erp_url,
            "CRM_ERP_PLATFORM_KEY": platform_key,
            "CRM_ADMIN_PASSWORD": current.admin_password,
        }
        crm = subprocess.run(
            [sys.executable, "-m", "app.migrate", "--control"],
            cwd=BACKEND,
            env=current.crm_env,
            capture_output=True,
            text=True,
        )
        assert crm.returncode == 0, crm.stdout + crm.stderr
        crm_log = tmp_path / "crm.log"
        current.processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(crm_port),
                ],
                cwd=BACKEND,
                env=current.crm_env,
                stdout=crm_log.open("wb"),
                stderr=subprocess.STDOUT,
            )
        )
        wait_http(f"{current.crm_url}/api/health", current.processes[-1], crm_log)
        for label, slug in current.slugs.items():
            provisioned = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.provider",
                    "provision",
                    "--request-id",
                    str(uuid.uuid4()),
                    "--slug",
                    slug,
                    "--company-name",
                    f"{label.title()} Trading {run}",
                    "--legal-name",
                    f"{label.title()} Trading {run} W.L.L.",
                    "--cr-number",
                    str(secrets.randbelow(10**8)).zfill(8),
                    "--fy-start",
                    f"{YEAR}-01-01",
                    "--admin-email",
                    f"owner@{slug}.example",
                ],
                cwd=BACKEND,
                env=current.crm_env,
                capture_output=True,
                text=True,
            )
            assert provisioned.returncode == 0, provisioned.stdout + provisioned.stderr
        yield current
    finally:
        cleanup(current)


class Browser:
    def __init__(self, stack: Stack, slug: str) -> None:
        self.stack, self.slug, self.host = stack, slug, f"{slug}.{BASE_DOMAIN}"
        self.client = httpx.Client(base_url=stack.crm_url, timeout=30)
        self.cookie: str | None = None
        self.setcookie = ""

    def req(self, method: str, path: str, body: object = None) -> httpx.Response:
        headers = {"Host": self.host, "Origin": f"http://{self.host}"}
        if self.cookie:
            headers["Cookie"] = f"crm_session={self.cookie}"
        response = self.client.request(method, "/api" + path, json=body, headers=headers)
        for raw in response.headers.get_list("set-cookie"):
            if raw.startswith("crm_session="):
                self.cookie = raw.split(";", 1)[0].split("=", 1)[1]
                self.setcookie = raw
        return response

    def login(self, email: str, password: str | None = None) -> httpx.Response:
        return self.req(
            "POST", "/auth/login", {"email": email, "password": password or self.stack.admin_password}
        )


def test_hosted_crm_erp_over_http(stack: Stack, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: object, detail: object = "") -> None:
        results.append((name, bool(ok), str(detail)[:300]))
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" -- {detail}"), flush=True)

    def q(db: str, query: str, *args: object) -> list[tuple[Any, ...]]:
        with psycopg.connect(db_url(db)) as connection:
            return list(connection.execute(query, args).fetchall())

    def ex(db: str, query: str, *args: object) -> None:
        with psycopg.connect(db_url(db)) as connection:
            connection.execute(query, args)

    def worker() -> str:
        done = subprocess.run(
            [sys.executable, "-m", "app.accounting_worker", "--once"],
            cwd=BACKEND,
            env={**stack.crm_env, "CRM_ACCOUNTING_WORKER_ENABLED": "true"},
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout + done.stderr

    def doc(b: Browser, kind: str, customer: int, amount: str, tax: str = "0") -> dict[str, Any]:
        body = {
            "kind": kind,
            "customer_id": customer,
            "date": str(date.today()),
            "due": str(date.today() + timedelta(days=30)),
            "tax_rate": tax,
            "items": [{"description": "Consulting", "quantity": "1", "unit_price": amount}],
        }
        response = b.req("POST", "/documents", body)
        assert response.status_code == 201, response.text
        return dict(response.json())

    def account_doc(
        b: Browser, kind: str, customer: int, amount: str, reason: str, invoice_id: int | None = None
    ) -> dict[str, Any]:
        body = {
            "kind": kind,
            "customer_id": customer,
            "invoice_id": invoice_id,
            "date": str(date.today()),
            "due": str(date.today()),
            "amount": amount,
            "reason": reason,
        }
        response = b.req("POST", "/documents", body)
        assert response.status_code == 201, response.text
        return dict(response.json())

    def approve(b: Browser, reviewer: Browser, ident: int) -> dict[str, Any]:
        assert b.req("POST", f"/documents/{ident}/actions", {"action": "submit"}).status_code == 200
        pending = [
            a["id"]
            for a in reviewer.req("GET", "/approvals").json()
            if a["document_id"] == ident and a["status"] == "pending"
        ]
        assert pending, f"no pending approval for document {ident}"
        response = reviewer.req("POST", f"/approvals/{pending[0]}/decision", {"decision": "approved"})
        assert response.status_code == 200, response.text
        return dict(response.json())

    erp, control = stack.erp_db, stack.control_db
    slug = stack.slugs
    ws = {
        label: q(
            control,
            "select id, db_name, erp_company_id, erp_binding_id from crm_workspaces where slug=%s",
            slug[label],
        )[0]
        for label in ("alpha", "beta")
    }
    monkeypatch.setenv("CRM_SECRET_KEY", stack.crm_env["CRM_SECRET_KEY"])
    from app.secretbox import decrypt

    cred = {
        label: decrypt(
            q(
                control,
                "select token_enc from crm_workspace_credentials where workspace_id=%s and status='active'",
                ws[label][0],
            )[0][0]
        )
        for label in ws
    }

    # 1. Onboarding: each client got its own ERP tenant/company, chart, fiscal year and posting profile.
    for label, (wid, _dbname, company_id, binding) in ws.items():
        rows = q(
            erp,
            "select t.slug, c.base_currency, b.status, b.chart_template from crm_workspace_bindings b "
            "join tenants t on t.id=b.tenant_id join companies c on c.id=b.company_id where b.workspace_id=%s",
            wid,
        )
        check(
            f"{label}: ERP binding/tenant/company created",
            rows == [(f"crm-{slug[label]}", "KWD", "active", "general_business_v1")],
            rows,
        )
        codes = {r[0] for r in q(erp, "select code from accounts where company_id=%s", company_id)}
        need = {
            "1110",
            "1120",
            "1150",
            "1210",
            "2110",
            "2300",
            "3100",
            "3200",
            "3900",
            "4100",
            "4900",
            "4950",
        }
        need |= {"5100", "6100"}
        check(f"{label}: general_business_v1 chart provisioned", need <= codes, sorted(need - codes))
        profile = dict(
            q(
                erp,
                "select p.role, a.code from crm_posting_profiles p join accounts a on a.id=p.account_id "
                "where p.company_id=%s",
                company_id,
            )
        )
        check(
            f"{label}: posting profile",
            profile
            == {
                "receivable": "1210",
                "revenue": "4100",
                "sales_returns": "4900",
                "receipts_clearing": "1150",
                "tax_payable": "2300",
                "opening_equity": "3900",
                "adjustments": "4950",
            },
            profile,
        )
        periods = q(erp, "select count(*) from periods where company_id=%s and status='OPEN'", company_id)[0][
            0
        ]
        check(f"{label}: FY{YEAR} with 12 open periods", periods == 12, periods)
        users = q(
            erp,
            "select count(*) from users u join crm_workspace_bindings b on b.tenant_id=u.tenant_id "
            "where b.workspace_id=%s",
            wid,
        )[0][0]
        check(f"{label}: ERP tenant has no human users", users == 0, users)
        creds = q(
            erp,
            "select count(*) from crm_service_credentials where binding_id=%s and status='active'",
            binding,
        )[0][0]
        check(f"{label}: one active service credential (hash only)", creds == 1, creds)
    check("ERP tenants/companies distinct", ws["alpha"][2] != ws["beta"][2])

    # 2. CRM sessions: login, reviewer, accounting state.
    A, B = Browser(stack, slug["alpha"]), Browser(stack, slug["beta"])
    check("alpha admin login", A.login(f"owner@{slug['alpha']}.example").status_code == 200)
    cookie = A.setcookie
    check(
        "session cookie host-only/Secure/HttpOnly/Strict",
        "Domain" not in cookie
        and "Secure" in cookie
        and "HttpOnly" in cookie
        and "samesite=strict" in cookie.lower(),
        cookie.split(";", 1)[-1],
    )
    check("beta admin login", B.login(f"owner@{slug['beta']}.example").status_code == 200)
    reviewers = {}
    for b in (A, B):
        email = f"reviewer-{secrets.token_hex(3)}@{b.slug}.example"
        created = b.req(
            "POST",
            "/users",
            {"name": "Reviewer", "email": email, "password": stack.admin_password, "role": "manager"},
        )
        assert created.status_code == 201, created.text
        reviewer = Browser(stack, b.slug)
        assert reviewer.login(email).status_code == 200
        reviewers[b.slug] = reviewer
    RA = reviewers[slug["alpha"]]
    status = A.req("GET", "/accounting/status").json()
    check(
        "alpha accounting enabled, not paused, bound to its ERP company",
        status["enabled"] and status["pausedReason"] is None and status["erp"]["companyId"] == ws["alpha"][2],
        status,
    )
    check(
        "no service token in browser responses",
        "nxc_" not in json.dumps(status) and "nxp_" not in json.dumps(status),
    )

    # 3. Non-posting activity: quote lifecycle, drafts, tasks, opportunities.
    customer_name = f"Gulf Retail {stack.run}"
    cust = A.req("POST", "/customers", {"name": customer_name}).json()["id"]
    quote = doc(A, "quote", cust, "250.000")
    approve(A, RA, quote["id"])
    for action in ("send", "accept"):
        response = A.req("POST", f"/documents/{quote['id']}/actions", {"action": action})
        assert response.status_code == 200, response.text
    draft = doc(A, "invoice", cust, "999.000")
    task = A.req(
        "POST", "/tasks", {"title": "Follow up", "customer_id": cust, "owner_id": 1, "due": str(date.today())}
    )
    assert task.status_code in (200, 201), task.text
    opportunity = A.req(
        "POST",
        "/opportunities",
        {
            "title": "Deal",
            "customer_id": cust,
            "value": "1000",
            "stage": "Won",
            "owner_id": 1,
            "closing_date": str(date.today()),
        },
    )
    check("won opportunity created", opportunity.status_code in (200, 201), opportunity.text[:200])

    def outbox(label: str) -> list[tuple[Any, ...]]:
        return q(
            ws[label][1],
            "select id, event_id, event_type, status, erp_journal_number, payload from crm_accounting_outbox "
            "order by id",
        )

    def journal_count(company_id: object) -> int:
        return int(q(erp, "select count(*) from journal_entries where company_id=%s", company_id)[0][0])

    def journal_lines(company_id: object, suffix: str) -> list[tuple[str, str, str, bool]]:
        found = q(
            erp,
            "select j.external_id, a.code, l.debit, l.credit, l.party_id is not null from journal_lines l "
            "join journal_entries j on j.id=l.entry_id join accounts a on a.id=l.account_id "
            "where j.company_id=%s order by j.number, l.line_no",
            company_id,
        )
        return sorted((c, str(d), str(cr), p) for e, c, d, cr, p in found if e.endswith(suffix))

    check(
        "quote lifecycle + draft + task/opportunity create no outbox rows",
        outbox("alpha") == [],
        outbox("alpha"),
    )

    # 4. Issued invoice and recorded payment enqueue and auto-post.
    inv = approve(A, RA, doc(A, "invoice", cust, "100.000", "5")["id"])
    check("invoice issued", inv.get("status") == "issued", inv.get("status"))
    pay = A.req(
        "POST",
        "/payments",
        {
            "customer_id": cust,
            "amount": "60.500",
            "date": str(date.today()),
            "reference": f"TRX-{stack.run}",
            "method": "Bank transfer",
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    check("payment recorded", pay.status_code == 201, pay.text)
    rows = outbox("alpha")
    check(
        "exactly two outbox events (invoice.issued, payment.received), pending",
        [(r[2], r[3]) for r in rows] == [("invoice.issued", "pending"), ("payment.received", "pending")],
        rows,
    )
    check("worker run 1", "Traceback" not in worker())
    rows = outbox("alpha")
    check(
        "both events posted with ERP journal numbers",
        [r[3] for r in rows] == ["posted"] * 2 and all(r[4] for r in rows),
        rows,
    )
    company = ws["alpha"][2]
    source_system = f"crm:{ws['alpha'][3]}"
    journals = q(
        erp,
        "select id, number, external_id, total_debit, total_credit, status, source_system, created_by_id "
        "from journal_entries where company_id=%s order by number",
        company,
    )
    check(
        "two balanced POSTED ERP journals in alpha company, service-created",
        len(journals) == 2
        and all(
            j[3] == j[4] and j[5] == "POSTED" and j[6] == source_system and j[7] is None for j in journals
        ),
        journals,
    )
    check("ERP external ids are the CRM event ids", [j[2] for j in journals] == [r[1] for r in rows])
    check(
        "invoice lines: Dr 1210 105 (party) / Cr 4100 100 / Cr 2300 5",
        journal_lines(company, "invoice.issued")
        == sorted(
            [
                ("1210", "105.000", "0.000", True),
                ("4100", "0.000", "100.000", False),
                ("2300", "0.000", "5.000", False),
            ]
        ),
        journal_lines(company, "invoice.issued"),
    )
    check(
        "payment lines: Dr 1150 60.5 / Cr 1210 60.5 (party)",
        journal_lines(company, "payment.received")
        == sorted([("1150", "60.500", "0.000", False), ("1210", "0.000", "60.500", True)]),
        journal_lines(company, "payment.received"),
    )
    parties = q(
        erp, "select external_ref, name, source_system from subledger_parties where company_id=%s", company
    )
    check(
        "customer subledger party auto-created",
        parties == [(str(cust), customer_name, source_system)],
        parties,
    )

    def hdr(label: str, workspace: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {cred[label]}", "X-Crm-Workspace-Id": workspace or ws[label][0]}

    def erp_balance(label: str, customer: int) -> object:
        url = f"{stack.erp_url}/api/v1/integrations/crm/customers/{customer}/balance"
        return httpx.get(url, headers=hdr(label)).json().get("balance")

    check(
        "ERP customer subledger balance = 44.500",
        erp_balance("alpha", cust) == "44.500",
        erp_balance("alpha", cust),
    )

    def getdoc(b: Browser, ident: int) -> dict[str, Any]:
        return next(d for d in b.req("GET", "/documents").json() if d["id"] == ident)

    detail = getdoc(A, inv["id"])
    check(
        "CRM invoice shows posted accounting badge",
        (detail.get("accounting") or {}).get("status") == "posted",
        detail.get("accounting"),
    )

    # 5. Duplicate retry: simulate a lost ERP response and re-send; ERP must replay, not double-post.
    ex(
        ws["alpha"][1],
        "update crm_accounting_outbox set status='pending', erp_journal_id=null, erp_journal_number=null, "
        "posted_at=null, next_attempt_at=now() where event_type='invoice.issued'",
    )
    worker()
    again = outbox("alpha")
    count = journal_count(company)
    check(
        "lost-response retry re-links same journal, no second journal",
        again[0][3] == "posted" and again[0][4] == rows[0][4] and count == 2,
        (again[0][3:5], count),
    )
    events = f"{stack.erp_url}/api/v1/integrations/crm/events"
    payload = rows[0][5]
    dup = httpx.post(events, headers={**hdr("alpha"), "Idempotency-Key": payload["eventId"]}, json=payload)
    check(
        "direct duplicate POST -> 200 duplicate:true",
        dup.status_code == 200 and dup.json().get("duplicate") is True,
        dup.text,
    )
    changed = {**payload, "amounts": {"net": "200.000", "tax": "10.000", "total": "210.000"}}
    conflict = httpx.post(
        events, headers={**hdr("alpha"), "Idempotency-Key": payload["eventId"]}, json=changed
    )
    check(
        "changed payload same eventId -> 409 CRM_EVENT_CONFLICT",
        conflict.status_code == 409 and conflict.json()["error"]["code"] == "CRM_EVENT_CONFLICT",
        conflict.text,
    )
    check("still exactly 2 journals", journal_count(company) == 2)

    # 6. Drafts never post.
    draft_now = getdoc(A, draft["id"])
    check(
        "draft invoice stays draft, no outbox/ERP journal",
        draft_now["status"] == "draft"
        and not q(
            ws["alpha"][1],
            "select 1 from crm_accounting_outbox where source_type='document' and source_id=%s",
            draft["id"],
        )
        and not q(
            erp, "select 1 from journal_entries where reference=%s", draft_now.get("number") or "__none__"
        ),
        draft_now["status"],
    )

    # 7. Issued credit note and refund enqueue and auto-post.
    credit = approve(A, RA, account_doc(A, "credit", cust, "21.000", "Returned goods", inv["id"])["id"])
    check("credit note issued", credit.get("status") == "issued", credit.get("status"))
    refund = approve(A, RA, account_doc(A, "refund", cust, "10.000", "Refund of unallocated payment")["id"])
    check("refund issued", refund.get("status") == "issued", refund.get("status"))
    rows = outbox("alpha")
    check(
        "credit note and refund each enqueue one pending event",
        [(r[2], r[3]) for r in rows[2:]] == [("credit_note.issued", "pending"), ("refund.paid", "pending")],
        [(r[2], r[3]) for r in rows],
    )
    worker()
    rows = outbox("alpha")
    check(
        "credit note and refund posted with ERP journal numbers",
        all(r[3] == "posted" and r[4] for r in rows),
        rows,
    )
    journals = q(
        erp,
        "select id, number, external_id, total_debit, total_credit, status, source_system, created_by_id "
        "from journal_entries where company_id=%s order by number",
        company,
    )
    check(
        "four balanced POSTED service-created journals in alpha company",
        len(journals) == 4
        and all(j[3] == j[4] and j[5] == "POSTED" and j[6] == source_system for j in journals),
        journals,
    )
    check(
        "credit note lines: Dr 4900 20 / Dr 2300 1 / Cr 1210 21 (party)",
        journal_lines(company, "credit_note.issued")
        == sorted(
            [
                ("4900", "20.000", "0.000", False),
                ("2300", "1.000", "0.000", False),
                ("1210", "0.000", "21.000", True),
            ]
        ),
        journal_lines(company, "credit_note.issued"),
    )
    check(
        "refund lines: Dr 1210 10 (party) / Cr 1150 10",
        journal_lines(company, "refund.paid")
        == sorted([("1210", "10.000", "0.000", True), ("1150", "0.000", "10.000", False)]),
        journal_lines(company, "refund.paid"),
    )
    check(
        "ERP customer balance after credit note and refund = 33.500",
        erp_balance("alpha", cust) == "33.500",
        erp_balance("alpha", cust),
    )
    alpha_journals = len(journals)

    # 8. Cross-client isolation.
    hostile = Browser(stack, slug["beta"])
    hostile.cookie = A.cookie
    check("alpha session cookie rejected on beta host", hostile.req("GET", "/customers").status_code == 401)
    spoofed = A.client.get(
        "/api/customers",
        headers={"Host": B.host, "X-Forwarded-Host": A.host, "Cookie": f"crm_session={A.cookie}"},
    )
    check(
        "alpha cookie not usable via spoofed X-Forwarded-Host",
        spoofed.status_code == 401,
        spoofed.status_code,
    )
    check(
        "beta user cannot sign in on alpha",
        Browser(stack, slug["alpha"]).login(f"owner@{slug['beta']}.example").status_code == 401,
    )
    check("beta sees none of alpha's customers", customer_name not in B.req("GET", "/customers").text)
    check("beta CRM reads only beta outbox", customer_name not in B.req("GET", "/accounting/outbox").text)
    connection_url = f"{stack.erp_url}/api/v1/integrations/crm/connection"
    cross = httpx.get(connection_url, headers=hdr("beta", ws["alpha"][0]))
    check("beta ERP token + alpha workspace header -> 401", cross.status_code == 401, cross.status_code)
    connection = httpx.get(connection_url, headers=hdr("beta")).json()
    check(
        "beta token scoped to beta company only",
        connection["companyId"] == ws["beta"][2],
        connection.get("companyId"),
    )
    check("beta token cannot read alpha customer balance", erp_balance("beta", cust) == "0.000")
    listed = httpx.get(f"{stack.erp_url}/api/v1/integrations/crm/journals", headers=hdr("beta")).text
    check("beta token journal list excludes alpha journals", all(str(j[0]) not in listed for j in journals))
    evil = {**payload, "eventId": payload["eventId"] + "x"}
    into_alpha = httpx.post(
        events, headers={**hdr("beta", ws["alpha"][0]), "Idempotency-Key": evil["eventId"]}, json=evil
    )
    check("beta token cannot post into alpha (401)", into_alpha.status_code == 401, into_alpha.status_code)
    no_workspace = httpx.post(
        events,
        headers={"Authorization": f"Bearer {cred['beta']}", "Idempotency-Key": evil["eventId"]},
        json=evil,
    )
    check(
        "token without workspace header cannot use /events",
        no_workspace.status_code == 401,
        no_workspace.status_code,
    )
    check("alpha ERP journals unchanged after attacks", journal_count(company) == alpha_journals)
    legacy = httpx.post(f"{stack.erp_url}/api/v1/integrations/crm/journals", headers=hdr("beta"), json={})
    check(
        "legacy POST /journals refused for hosted credential",
        legacy.status_code in (401, 403, 404),
        legacy.status_code,
    )
    platform = httpx.put(
        f"{stack.erp_url}/api/v1/platform/crm-workspaces/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {cred['alpha']}"},
        json={},
    )
    check("service token cannot use platform API", platform.status_code in (401, 403), platform.status_code)

    # 9. Beta posts its own invoice for its own customer into its own company.
    beta_customer = B.req("POST", "/customers", {"name": f"Beta Client {stack.run}"}).json()["id"]
    approve(B, reviewers[slug["beta"]], doc(B, "invoice", beta_customer, "40.000")["id"])
    worker()
    beta_journals = journal_count(ws["beta"][2])
    check(
        "beta invoice posted only to beta company",
        beta_journals == 1 and outbox("beta")[0][3] == "posted" and journal_count(company) == alpha_journals,
        beta_journals,
    )
    same_ref = q(
        erp, "select company_id from subledger_parties where external_ref=%s order by company_id", str(cust)
    )
    check(
        "same customer ref kept separate per company",
        len(same_ref) == (2 if beta_customer == cust else 1),
        same_ref,
    )
    trial = A.req("GET", f"/accounting/trial-balance?start={YEAR}-01-01&end={YEAR}-12-31")
    check(
        "alpha CRM ERP trial-balance view works (server-side token)",
        trial.status_code == 200 and "nxc_" not in trial.text,
        trial.status_code,
    )

    (tmp_path / "results.json").write_text(json.dumps(results, indent=1))
    failed = [name for name, ok, _ in results if not ok]
    print(json.dumps({"passed": len(results) - len(failed), "failed": len(failed)}))
    assert not failed, failed
