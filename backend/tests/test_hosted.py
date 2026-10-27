import hashlib
import json
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app import accounting_worker, main, migrate
from app.control import Workspace as ControlWorkspace
from app.control import WorkspaceCredential, control_engine, control_sessions, reset_control
from app.models import AccountingOutbox, AccountingState, Customer, LedgerEntry
from app.outbox import enqueue_for_ledger
from app.provisioning import (
    ErpPlatform,
    ProvisionError,
    ProvisionRequest,
    provision_workspace,
    rotate_credential,
    suspend_workspace,
)
from app.tenancy import dispose_all, make_workspace_engine, workspace_sessions

PASSWORD = "HostedWorkspace!2026"
PLATFORM_KEY = "nxp_test_secretkey"
PROVIDER_KEY = "provider-test-key-0123456789"
REVIEWERS: dict[str, TestClient] = {}


@dataclass
class FakeErp:
    bindings: dict[str, dict[str, str]] = field(default_factory=dict)
    credentials: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    journals: dict[tuple[str, str], tuple[str, int]] = field(default_factory=dict)
    event_responses: list[httpx.Response] = field(default_factory=list)
    lose_next: int = 0
    calls: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def authenticated(self, request: httpx.Request) -> str | None:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        digest = hashlib.sha256(token.encode()).hexdigest()
        workspace = request.headers.get("x-crm-workspace-id", "")
        for workspace_id, token_hash, status in self.credentials.values():
            if token_hash == digest and status == "active" and workspace_id == workspace:
                return workspace
        return None

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            return self.handle(request)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if path.startswith("/api/v1/platform/"):
            if request.headers.get("authorization") != f"Bearer {PLATFORM_KEY}":
                return httpx.Response(401, json={"error": {"code": "UNAUTHENTICATED", "message": "no"}})
            parts = path.removeprefix("/api/v1/platform/crm-workspaces/").split("/")
            workspace_id = parts[0]
            if len(parts) == 1:
                body = json.loads(request.content)
                assert body["currency"] == "KWD" and body["chartTemplate"] == "general_business_v1"
                created = workspace_id not in self.bindings
                ids = self.bindings.setdefault(
                    workspace_id,
                    {key: str(uuid.uuid4()) for key in ("bindingId", "tenantId", "companyId")},
                )
                return httpx.Response(
                    201 if created else 200, json={**ids, "workspaceId": workspace_id, "currency": "KWD"}
                )
            if parts[1] == "credentials":
                credential_id = parts[2]
                if request.method == "DELETE":
                    if credential_id in self.credentials:
                        owner, token_hash, _ = self.credentials[credential_id]
                        self.credentials[credential_id] = (owner, token_hash, "revoked")
                    return httpx.Response(204)
                token_hash = json.loads(request.content)["tokenSha256"]
                existing = self.credentials.get(credential_id)
                if existing and existing[1] != token_hash:
                    return httpx.Response(
                        409, json={"error": {"code": "CREDENTIAL_CONFLICT", "message": "x"}}
                    )
                self.credentials[credential_id] = (workspace_id, token_hash, "active")
                return httpx.Response(200 if existing else 201, json={"credentialId": credential_id})
            return httpx.Response(200, json={"status": "suspended"})
        workspace_id = self.authenticated(request)
        if workspace_id is None:
            return httpx.Response(401, json={"error": {"code": "UNAUTHENTICATED", "message": "no"}})
        if path == "/api/v1/integrations/crm/connection":
            ids = self.bindings[workspace_id]
            return httpx.Response(
                200, json={**ids, "workspaceId": workspace_id, "currency": "KWD", "companyName": "ERP Co"}
            )
        assert path == "/api/v1/integrations/crm/events"
        if self.event_responses:
            return self.event_responses.pop(0)
        body = json.loads(request.content)
        assert request.headers["idempotency-key"] == body["eventId"]
        digest = hashlib.sha256(request.content).hexdigest()
        key = (workspace_id, body["eventId"])
        if key in self.journals:
            if self.journals[key][0] != digest:
                return httpx.Response(
                    409, json={"error": {"code": "CRM_EVENT_CONFLICT", "message": "changed"}}
                )
            journal = {"journalId": str(uuid.uuid4()), "number": self.journals[key][1], "duplicate": True}
            return httpx.Response(200, json=journal)
        self.journals[key] = (digest, len(self.journals) + 1)
        if self.lose_next:
            self.lose_next -= 1
            raise httpx.ReadTimeout("lost response", request=request)
        journal = {"journalId": str(uuid.uuid4()), "number": self.journals[key][1], "duplicate": False}
        return httpx.Response(201, json=journal)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def erp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeErp]:
    values = {
        "CRM_TENANCY": "hosted",
        "CRM_CONTROL_DATABASE_URL": f"sqlite:///{tmp_path / 'control.db'}",
        "CRM_WORKSPACE_DATABASE_URL": f"sqlite:///{tmp_path / 'workspaces'}",
        "CRM_BASE_DOMAIN": "crm.test",
        "CRM_SECRET_KEY": "s" * 40,
        "CRM_UPLOAD_DIR": str(tmp_path / "uploads"),
        "CRM_ERP_URL": "https://erp.test",
        "CRM_ERP_PLATFORM_KEY": PLATFORM_KEY,
        "CRM_PROVIDER_HOST": "provider.crm.test",
        "CRM_PROVIDER_API_KEY_SHA256": hashlib.sha256(PROVIDER_KEY.encode()).hexdigest(),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CRM_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("CRM_ERP_TOKEN", raising=False)
    reset_control()
    dispose_all()
    migrate.upgrade(control_engine(), "control")
    main.failed_logins.clear()
    yield FakeErp()
    for client in REVIEWERS.values():
        client.__exit__(None, None, None)
    REVIEWERS.clear()
    dispose_all()
    reset_control()


def request(slug: str, request_id: uuid.UUID | None = None, **changes: object) -> ProvisionRequest:
    body: dict[str, object] = {
        "request_id": request_id or uuid.uuid4(),
        "slug": slug,
        "company_name": f"{slug.title()} Trading",
        "legal_name": f"{slug.title()} Trading Co. W.L.L.",
        "cr_number": "123456",
        "fy_start": "2026-01-01",
        "admin_email": f"owner@{slug}.example",
    }
    return ProvisionRequest.model_validate({**body, **changes})


def provision(erp: FakeErp, slug: str) -> dict[str, object]:
    return provision_workspace(request(slug), "test", ErpPlatform(erp.client()), admin_password=PASSWORD)


def control_workspace(slug: str) -> ControlWorkspace:
    with control_sessions()() as control:
        workspace = control.scalar(select(ControlWorkspace).where(ControlWorkspace.slug == slug))
        assert workspace is not None
        return workspace


def sessions_for(slug: str) -> sessionmaker[Session]:
    return workspace_sessions(control_workspace(slug))


def browser(slug: str) -> TestClient:
    origin = f"https://{slug}.crm.test"
    return TestClient(main.app, base_url=origin, headers={"Origin": origin})


def login(client: TestClient, slug: str) -> None:
    response = client.post("/api/auth/login", json={"email": f"owner@{slug}.example", "password": PASSWORD})
    assert response.status_code == 200, response.text


def issue_invoice(client: TestClient, customer_id: int, amount: str = "100.000") -> dict[str, object]:
    body = {
        "kind": "invoice",
        "customer_id": customer_id,
        "date": str(date.today()),
        "due": str(date.today() + timedelta(days=30)),
        "items": [{"description": "Service", "quantity": "1", "unit_price": amount}],
    }
    created = client.post("/api/documents", json=body)
    assert created.status_code == 201, created.text
    return approve(client, created.json()["id"])


def reviewer(client: TestClient) -> TestClient:
    slug = client.base_url.host.split(".")[0]
    if slug not in REVIEWERS:
        email = f"reviewer@{slug}.example"
        body = {"name": "Reviewer", "email": email, "password": PASSWORD, "role": "manager"}
        assert client.post("/api/users", json=body).status_code == 201
        manager = browser(slug)
        manager.__enter__()
        assert manager.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
        REVIEWERS[slug] = manager
    return REVIEWERS[slug]


def approve(client: TestClient, identifier: int) -> dict[str, object]:
    assert client.post(f"/api/documents/{identifier}/actions", json={"action": "submit"}).status_code == 200
    approval = next(
        value["id"]
        for value in client.get("/api/approvals").json()
        if value["document_id"] == identifier and value["status"] == "pending"
    )
    response = reviewer(client).post(f"/api/approvals/{approval}/decision", json={"decision": "approved"})
    assert response.status_code == 200, response.text
    return response.json()


def outbox_rows(slug: str) -> list[AccountingOutbox]:
    with sessions_for(slug)() as db:
        return list(db.scalars(select(AccountingOutbox).order_by(AccountingOutbox.id)))


def test_provisioning_creates_isolated_active_workspace(erp: FakeErp) -> None:
    result = provision(erp, "acme")
    assert result["status"] == "active" and result["step"] == "active"
    assert "inviteUrl" not in result and "nxc_" not in json.dumps(result)
    workspace = control_workspace("acme")
    assert (
        Path(workspace.upload_root).is_dir() and oct(Path(workspace.upload_root).stat().st_mode)[-3:] == "700"
    )
    with sessions_for("acme")() as db:
        state = db.get(AccountingState, 1)
        assert state and state.enabled and state.cutoff_ledger_id == 0
        assert state.binding_id == erp.bindings[workspace.id]["bindingId"]
        settings = db.execute(text("SELECT currency, decimals, is_demo FROM crm_settings")).one()
        assert tuple(settings) == ("KWD", 3, 0)
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0002_accounting_outbox"
    names = inspect(control_engine()).get_table_names()
    assert "crm_customers" not in names and "crm_workspaces" in names


def test_invite_only_admin_can_set_password_once(erp: FakeErp) -> None:
    result = provision_workspace(request("invited"), "test", ErpPlatform(erp.client()))
    token = str(result["inviteUrl"]).split("invite=")[1]
    with browser("invited") as client:
        denied = client.post("/api/auth/login", json={"email": "owner@invited.example", "password": PASSWORD})
        assert denied.status_code == 401
        accepted = client.post("/api/auth/accept-invite", json={"token": token, "password": PASSWORD})
        assert accepted.status_code == 200, accepted.text
        assert (
            client.post("/api/auth/accept-invite", json={"token": token, "password": PASSWORD}).status_code
            == 400
        )
        login(client, "invited")


def test_workspaces_isolate_users_sessions_cookies_files_and_customers(erp: FakeErp) -> None:
    provision(erp, "alpha")
    provision(erp, "beta")
    with browser("alpha") as alpha, browser("beta") as beta, browser("ghost") as ghost:
        login(alpha, "alpha")
        login(beta, "beta")
        a = alpha.post("/api/customers", json={"name": "Alpha Customer"}).json()
        b = beta.post("/api/customers", json={"name": "Beta Customer"}).json()
        assert a["id"] == b["id"] == 1
        assert [value["name"] for value in alpha.get("/api/customers").json()] == ["Alpha Customer"]
        assert [value["name"] for value in beta.get("/api/customers").json()] == ["Beta Customer"]
        cookie = alpha.cookies.get("crm_session")
        assert cookie and cookie.startswith(control_workspace("alpha").id + ".")
        header = alpha.post(
            "/api/auth/login", json={"email": "owner@alpha.example", "password": PASSWORD}
        ).headers["set-cookie"]
        assert (
            "HttpOnly" in header
            and "Secure" in header
            and "SameSite=strict" in header.replace("Strict", "strict")
        )
        assert "Domain" not in header
        with browser("beta") as stolen:
            stolen.cookies.set("crm_session", cookie)
            assert stolen.get("/api/auth/me").status_code == 401
        beta_login = beta.post("/api/auth/login", json={"email": "owner@alpha.example", "password": PASSWORD})
        assert beta_login.status_code == 401
        upload = alpha.post(
            f"/api/customers/{a['id']}/attachments",
            files={"file": ("contract.txt", b"alpha secret", "text/plain")},
        )
        assert upload.status_code == 201, upload.text
        attachment = upload.json()["id"]
        assert alpha.get(f"/api/attachments/{attachment}").content == b"alpha secret"
        assert beta.get(f"/api/attachments/{attachment}").status_code == 404
        files = list(Path(control_workspace("alpha").upload_root).iterdir())
        assert len(files) == 1
        assert ghost.get("/api/auth/me").status_code == 404
        forwarded = TestClient(main.app, base_url="https://ghost.crm.test")
        response = forwarded.get("/api/auth/me", headers={"X-Forwarded-Host": "alpha.crm.test"})
        assert response.status_code == 404
        cross = alpha.post("/api/customers", json={"name": "X"}, headers={"Origin": "https://beta.crm.test"})
        assert cross.status_code == 403
        assert alpha.post("/api/accounting/activate", json={}).status_code == 410


def test_suspended_workspace_is_not_found_and_skipped(erp: FakeErp) -> None:
    provision(erp, "paused")
    suspend_workspace("paused", "test", ErpPlatform(erp.client()))
    with browser("paused") as client:
        assert client.get("/api/auth/me").status_code == 404
    assert accounting_worker.run_once(erp.client(), "w1") == {}


def test_only_finalized_financial_events_enter_outbox(erp: FakeErp) -> None:
    provision(erp, "books")
    with browser("books") as client:
        login(client, "books")
        customer = client.post("/api/customers", json={"name": "Buyer"}).json()["id"]
        me = client.get("/api/auth/me").json()
        owner = me.get("id") or me["user"]["id"]
        quote = client.post(
            "/api/documents",
            json={
                "kind": "quote",
                "customer_id": customer,
                "date": str(date.today()),
                "due": str(date.today() + timedelta(days=10)),
                "items": [{"description": "Quote", "quantity": "1", "unit_price": "50.000"}],
            },
        ).json()
        approve(client, quote["id"])
        draft = client.post(
            "/api/documents",
            json={
                "kind": "invoice",
                "customer_id": customer,
                "date": str(date.today()),
                "due": str(date.today()),
                "items": [{"description": "Draft", "quantity": "1", "unit_price": "10.000"}],
            },
        )
        assert draft.status_code == 201
        task = {"title": "Call", "customer_id": customer, "owner_id": owner, "due": str(date.today())}
        assert client.post("/api/tasks", json=task).status_code == 201
        opportunity = {"title": "Deal", "customer_id": customer, "owner_id": owner, "value": "5"}
        assert client.post("/api/opportunities", json=opportunity).status_code == 201
        assert outbox_rows("books") == []

        invoice = issue_invoice(client, customer, "115.000")
        rows = outbox_rows("books")
        assert [row.event_type for row in rows] == ["invoice.issued"]
        payload = rows[0].payload
        workspace_id = control_workspace("books").id
        assert payload["eventId"] == f"crmws:{workspace_id}:document:{invoice['id']}:invoice.issued"
        assert payload["currency"] == "KWD" and payload["amounts"]["total"] == "115.000"
        assert payload["customer"] == {"ref": str(customer), "name": "Buyer", "kind": "company"}
        assert (
            rows[0].payload_hash
            == hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            ).hexdigest()
        )
        paid = client.post(
            "/api/payments",
            json={
                "customer_id": customer,
                "amount": "15.000",
                "date": str(date.today()),
                "reference": "Bank receipt",
                "idempotency_key": str(uuid.uuid4()),
                "allocations": [{"invoice_id": invoice["id"], "amount": "15.000"}],
            },
        )
        assert paid.status_code == 201, paid.text
        credit = client.post(
            "/api/documents",
            json={
                "kind": "credit",
                "customer_id": customer,
                "date": str(date.today()),
                "due": str(date.today()),
                "amount": "23.000",
                "reason": "Return",
                "invoice_id": invoice["id"],
            },
        ).json()
        approve(client, credit["id"])
        for kind in ["adjustment", "opening"]:
            blocked = client.post(
                "/api/documents",
                json={
                    "kind": kind,
                    "customer_id": customer,
                    "date": str(date.today()),
                    "due": str(date.today()),
                    "amount": "7.000",
                    "reason": "Manual",
                },
            )
            assert blocked.status_code == 409 and "hosted workspaces" in blocked.json()["detail"]
        assert client.get("/api/workspace").json()["manual_ledger_kinds"] == ["adjustment", "opening"]
        rows = outbox_rows("books")
        assert [(row.event_type, row.status) for row in rows] == [
            ("invoice.issued", "pending"),
            ("payment.received", "pending"),
            ("credit_note.issued", "pending"),
        ]
        assert rows[1].payload["amounts"] == {"net": "15.000", "tax": "0.000", "total": "15.000"}
        assert rows[2].payload["source"]["invoiceReference"] == invoice["number"]
        documents = client.get("/api/documents").json()
        issued = next(value for value in documents if value["id"] == invoice["id"])
        assert issued["accounting"]["status"] == "pending"
        status = client.get("/api/accounting/status").json()
        assert (
            status["mode"] == "hosted"
            and status["counts"]["pending"] == 3
            and status["counts"]["skipped"] == 0
        )
        queue = client.get("/api/accounting/outbox").json()
        assert {row["status"] for row in queue} == {"pending"}
        for response in [status, queue, documents]:
            assert "nxc_" not in json.dumps(response) and "nxp_" not in json.dumps(response)


def test_outbox_row_rolls_back_with_ledger_row(erp: FakeErp) -> None:
    provision(erp, "atomic")
    with sessions_for("atomic")() as db:
        db.add(Customer(id=1, name="Rollback"))
        db.commit()
        from app.models import Payment

        payment = Payment(
            customer_id=1,
            amount=1000,
            date=date.today(),
            reference="R",
            method="Bank",
            idempotency_key="k",
            request_fingerprint="f",
            created_by=1,
        )
        db.add(payment)
        db.flush()
        entry = LedgerEntry(
            customer_id=1, payment_id=payment.id, date=date.today(), description="P", credit=1000
        )
        db.add(entry)
        db.flush()
        assert enqueue_for_ledger(db, entry) is not None
        db.rollback()
        assert db.scalar(select(func.count(AccountingOutbox.id))) == 0
        assert db.scalar(select(func.count(LedgerEntry.id))) == 0


def seeded(erp: FakeErp, slug: str, count: int = 1) -> TestClient:
    provision(erp, slug)
    client = browser(slug)
    client.__enter__()
    login(client, slug)
    customer = client.post("/api/customers", json={"name": "Buyer"}).json()["id"]
    for _ in range(count):
        issue_invoice(client, customer)
    return client


def test_worker_posts_once_and_survives_lost_and_duplicate_responses(erp: FakeErp) -> None:
    client = seeded(erp, "worker", 2)
    erp.lose_next = 1
    accounting_worker.run_once(erp.client(), "w1")
    rows = outbox_rows("worker")
    assert [row.status for row in rows] == ["retrying", "posted"]
    assert rows[0].last_error_code == "ERP_UNREACHABLE" or rows[0].last_http_status is None
    with sessions_for("worker")() as db:
        db.execute(text("UPDATE crm_accounting_outbox SET next_attempt_at = '2000-01-01 00:00:00'"))
        db.commit()
    accounting_worker.run_once(erp.client(), "w1")
    rows = outbox_rows("worker")
    assert [row.status for row in rows] == ["posted", "posted"]
    assert len(erp.journals) == 2 and rows[0].erp_journal_number == 1
    status = client.get("/api/accounting/status").json()
    assert status["counts"]["posted"] == 2 and status["lastPostedAt"]
    client.__exit__(None, None, None)


def test_concurrent_workers_produce_one_journal_per_event(erp: FakeErp) -> None:
    seeded(erp, "race", 5).__exit__(None, None, None)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda name: accounting_worker.run_once(erp.client(), name), ["a", "b", "c", "d"]))
    assert [row.status for row in outbox_rows("race")] == ["posted"] * 5
    assert len(erp.journals) == 5


def test_expired_lease_is_reclaimed(erp: FakeErp) -> None:
    seeded(erp, "lease").__exit__(None, None, None)
    sessions = sessions_for("lease")
    claimed = accounting_worker.claim(sessions, "crashed")
    assert len(claimed) == 1 and accounting_worker.claim(sessions, "other") == []
    with sessions() as db:
        db.execute(text("UPDATE crm_accounting_outbox SET lease_expires_at = '2000-01-01 00:00:00'"))
        db.commit()
    accounting_worker.run_once(erp.client(), "w2")
    row = outbox_rows("lease")[0]
    assert row.status == "posted" and row.lease_owner is None and row.attempts == 2


def error(status: int, code: str, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": code, "message": f"{code} detail"}}, headers=headers
    )


@pytest.mark.parametrize(
    ("response", "status", "code"),
    [
        (error(409, "CRM_EVENT_CONFLICT"), "conflict", "CRM_EVENT_CONFLICT"),
        (error(422, "PERIOD_CLOSED"), "blocked", "PERIOD_CLOSED"),
        (error(400, "VALIDATION_ERROR"), "blocked", "VALIDATION_ERROR"),
        (error(429, "RATE_LIMITED", {"Retry-After": "5"}), "retrying", "RATE_LIMITED"),
        (error(503, "UNAVAILABLE"), "retrying", "UNAVAILABLE"),
        (httpx.Response(201, json={"journalId": "nope", "number": 0}), "retrying", "ERP_BAD_RESPONSE"),
        (error(403, "POSTING_DISABLED"), "held", "POSTING_DISABLED"),
        (error(401, "UNAUTHENTICATED"), "held", "CREDENTIAL_REJECTED"),
    ],
)
def test_worker_error_classes(erp: FakeErp, response: httpx.Response, status: str, code: str) -> None:
    client = seeded(erp, "errors", 2)
    erp.event_responses.append(response)
    accounting_worker.run_once(erp.client(), "w1")
    rows = outbox_rows("errors")
    assert rows[0].status == status and rows[0].last_error_code == code
    assert rows[0].last_error_message is None or "nxc_" not in rows[0].last_error_message
    with sessions_for("errors")() as db:
        state = db.get(AccountingState, 1)
        assert state is not None
        if status == "held":
            assert state.paused_reason == {"POSTING_DISABLED": "ERP_POSTING_DISABLED"}.get(code, code)
            assert rows[1].status == "pending" and rows[0].attempts == 0
            banner = client.get("/api/accounting/status").json()
            assert banner["pausedReason"] == state.paused_reason
        else:
            assert state.paused_reason is None
            assert rows[1].status == "posted"
    if status in {"blocked", "held", "retrying"}:
        retry = client.post(f"/api/accounting/outbox/{rows[0].id}/retry")
        assert retry.status_code == 200, retry.text
        assert outbox_rows("errors")[0].status == "pending"
    else:
        assert client.post(f"/api/accounting/outbox/{rows[0].id}/retry").status_code == 409
    client.__exit__(None, None, None)


def test_retry_requires_finance_or_admin_role(erp: FakeErp) -> None:
    client = seeded(erp, "roles")
    response = client.post(
        "/api/users",
        json={"name": "Sales", "email": "sales@roles.example", "password": PASSWORD, "role": "sales"},
    )
    assert response.status_code == 201, response.text
    with browser("roles") as sales:
        assert (
            sales.post(
                "/api/auth/login", json={"email": "sales@roles.example", "password": PASSWORD}
            ).status_code
            == 200
        )
        assert sales.get("/api/accounting/status").status_code == 403
        assert sales.get("/api/accounting/outbox").status_code == 403
        row = outbox_rows("roles")[0]
        assert sales.post(f"/api/accounting/outbox/{row.id}/retry").status_code == 403
    response = client.post(
        "/api/users",
        json={"name": "Manager", "email": "manager@roles.example", "password": PASSWORD, "role": "manager"},
    )
    assert response.status_code == 201, response.text
    with browser("roles") as manager:
        login = {"email": "manager@roles.example", "password": PASSWORD}
        assert manager.post("/api/auth/login", json=login).status_code == 200
        assert manager.get("/api/accounting/status").status_code == 200
        assert manager.post("/api/accounting/sync").status_code == 403
        assert manager.post(f"/api/accounting/outbox/{row.id}/retry").status_code == 403
    assert client.post("/api/accounting/sync").status_code == 200
    client.__exit__(None, None, None)


def test_login_throttle_survives_a_flood_of_new_keys(erp: FakeErp, monkeypatch: pytest.MonkeyPatch) -> None:
    seeded(erp, "flood").__exit__(None, None, None)
    monkeypatch.setattr(main, "LOGIN_THROTTLE_MAX_KEYS", 20)
    monkeypatch.setattr(main, "LOGIN_ADDRESS_LIMIT", 1000)
    with browser("flood") as attacker:
        target = {"email": "owner@flood.example", "password": "wrong-password-123"}
        for _ in range(10):
            assert attacker.post("/api/auth/login", json=target).status_code == 401
        assert attacker.post("/api/auth/login", json=target).status_code == 429
        for index in range(40):
            other = {"email": f"random{index}@flood.example", "password": "wrong-password-123"}
            assert attacker.post("/api/auth/login", json=other).status_code == 401
        assert attacker.post("/api/auth/login", json=target).status_code == 429
    assert len(main.failed_logins) <= 20


def test_login_throttle_limits_failures_per_address(erp: FakeErp, monkeypatch: pytest.MonkeyPatch) -> None:
    seeded(erp, "spray").__exit__(None, None, None)
    monkeypatch.setattr(main, "LOGIN_ADDRESS_LIMIT", 5)
    with browser("spray") as attacker:
        for index in range(5):
            other = {"email": f"random{index}@spray.example", "password": "wrong-password-123"}
            assert attacker.post("/api/auth/login", json=other).status_code == 401
        owner = {"email": "owner@spray.example", "password": PASSWORD}
        assert attacker.post("/api/auth/login", json=owner).status_code == 429


@pytest.mark.parametrize(
    "step", ["control_row", "database", "migrate", "settings", "erp_binding", "credential", "verify", "admin"]
)
def test_provisioning_resumes_after_failure_at_every_step(erp: FakeErp, step: str) -> None:
    body = request("resume")

    def explode(current: str) -> None:
        if current == step:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        provision_workspace(body, "test", ErpPlatform(erp.client()), PASSWORD, fail_at=explode)
    workspace = control_workspace("resume")
    assert workspace.status == "failed" and workspace.last_error == "RuntimeError"
    result = provision_workspace(body, "test", ErpPlatform(erp.client()), PASSWORD)
    assert result["status"] == "active"
    again = provision_workspace(body, "test", ErpPlatform(erp.client()), PASSWORD)
    assert again["workspaceId"] == result["workspaceId"]
    with control_sessions()() as control:
        credentials = list(control.scalars(select(WorkspaceCredential)))
    assert [value.status for value in credentials] == ["active"]
    assert len(erp.bindings) == 1 and len(erp.credentials) == 1
    with sessions_for("resume")() as db:
        assert db.scalar(text("SELECT count(*) FROM crm_users")) == 1


def test_concurrent_same_request_converges_and_conflicts_are_rejected(erp: FakeErp) -> None:
    body = request("twins")
    with ThreadPoolExecutor(4) as pool:
        results = list(
            pool.map(
                lambda _: provision_workspace(body, "test", ErpPlatform(erp.client()), PASSWORD), range(4)
            )
        )
    assert {value["workspaceId"] for value in results} == {results[0]["workspaceId"]}
    assert len(erp.bindings) == 1 and len(erp.credentials) == 1
    with control_sessions()() as control:
        assert control.scalar(select(func.count(ControlWorkspace.id))) == 1
    with pytest.raises(ProvisionError) as changed:
        provision_workspace(
            request("twins", body.request_id, company_name="Other"), "test", ErpPlatform(erp.client())
        )
    assert changed.value.status == 409 and changed.value.code == "WORKSPACE_REQUEST_CONFLICT"
    with pytest.raises(ProvisionError) as taken:
        provision_workspace(request("twins"), "test", ErpPlatform(erp.client()))
    assert taken.value.code == "SLUG_TAKEN"
    with pytest.raises(ValueError):
        request("admin")


def test_credential_rotation_keeps_one_active_and_revokes_old(erp: FakeErp) -> None:
    provision(erp, "rotate")
    with control_sessions()() as control:
        old = control.scalar(select(WorkspaceCredential.id))
    result = rotate_credential("rotate", "test", ErpPlatform(erp.client()))
    assert result["revoked"] == [old] and "nxc_" not in json.dumps(result)
    with control_sessions()() as control:
        statuses = {value.id: value.status for value in control.scalars(select(WorkspaceCredential))}
    assert statuses[old] == "revoked" and list(statuses.values()).count("active") == 1
    assert erp.credentials[old][2] == "revoked"


def test_provider_http_surface_is_host_and_key_bound(erp: FakeErp) -> None:
    body = request("apiclient").model_dump(mode="json")
    with TestClient(main.app, base_url="https://provider.crm.test") as provider:
        assert provider.post("/provider/v1/workspaces", json=body).status_code == 401
        wrong = provider.post("/provider/v1/workspaces", json=body, headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        auth = {"Authorization": f"Bearer {PROVIDER_KEY}"}
        accepted = provider.post("/provider/v1/workspaces", json=body, headers=auth)
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["status"] == "provisioning" and "set-cookie" not in accepted.headers
        assert provider.post("/provider/v1/workspaces", json=body, headers=auth).json() == accepted.json()
        changed = provider.post(
            "/provider/v1/workspaces", json={**body, "company_name": "Changed"}, headers=auth
        )
        assert changed.status_code == 409
        state = provider.get(f"/provider/v1/workspaces/{accepted.json()['workspaceId']}", headers=auth)
        assert state.status_code == 200 and "nxc_" not in state.text and "nxp_" not in state.text
        assert provider.get("/api/auth/me").status_code == 404
        preflight = provider.options("/provider/v1/workspaces", headers={"Origin": "https://evil.test"})
        assert "access-control-allow-origin" not in preflight.headers
    with browser("apiclient") as workspace_host:
        response = workspace_host.post("/provider/v1/workspaces", json=body, headers=auth)
        assert response.status_code == 404


def test_migration_stamps_legacy_database_and_adds_outbox(tmp_path: Path) -> None:
    from app.models import Base

    engine = make_workspace_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(
        engine, tables=[t for t in Base.metadata.sorted_tables if "accounting" not in t.name]
    )
    with Session(engine) as db:
        db.add(Customer(id=1, name="Kept"))
        db.commit()
    assert migrate.stamp_existing(engine)
    migrate.upgrade(engine)
    names = inspect(engine).get_table_names()
    assert {"crm_accounting_outbox", "crm_accounting_state", "alembic_version"} <= set(names)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT name FROM crm_customers WHERE id = 1")) == "Kept"
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002_accounting_outbox"
    engine.dispose()


def test_secret_rotation_reads_values_from_previous_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import secretbox

    monkeypatch.setenv("CRM_SECRET_KEY", "a" * 40)
    stored = secretbox.encrypt("nxc_value")
    monkeypatch.setenv("CRM_SECRET_KEY", "b" * 40)
    monkeypatch.setenv("CRM_SECRET_KEY_PREVIOUS", "a" * 40)
    assert secretbox.decrypt(stored) == "nxc_value"
    monkeypatch.delenv("CRM_SECRET_KEY_PREVIOUS")
    with pytest.raises(RuntimeError):
        secretbox.decrypt(stored)
