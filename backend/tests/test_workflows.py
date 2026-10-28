import csv
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import Workspace
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.types import Message, Receive, Scope, Send

from app import main
from app.models import Customer, LedgerEntry, LoginSession, Payment, Settings
from app.services import minor


def test_writes_commit_before_a_success_response_is_sent(workspace: Workspace) -> None:
    with workspace.sessions() as db:
        session_count = db.scalar(select(func.count(LoginSession.token_hash))) or 0

    class Observer:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            async def inspect(message: Message) -> None:
                if message["type"] == "http.response.start" and scope["method"] == "POST":
                    with workspace.sessions() as db:
                        if scope["path"] == "/api/auth/login" and message["status"] == 200:
                            assert db.scalar(select(func.count(LoginSession.token_hash))) == session_count + 1
                        if scope["path"] == "/api/customers" and message["status"] == 201:
                            assert (
                                db.scalar(select(Customer).where(Customer.name == "Committed Customer"))
                                is not None
                            )
                await send(message)

            await main.app(scope, receive, inspect)

    with TestClient(Observer()) as client:
        assert (
            client.post(
                "/api/auth/login", json={"email": "admin@crm.local", "password": "IntegrationTest!2026"}
            ).status_code
            == 200
        )
        assert client.post("/api/customers", json={"name": "Committed Customer"}).status_code == 201


def create_customer(workspace: Workspace, name: str = "Test Customer") -> int:
    response = workspace.admin.post("/api/customers", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def payload(customer_id: int, kind: str = "invoice", amount: str = "100.00") -> dict[str, object]:
    return {
        "kind": kind,
        "customer_id": customer_id,
        "date": str(date.today()),
        "due": str(date.today() + timedelta(days=30)),
        "items": [{"description": "Professional service", "quantity": "1", "unit_price": amount}],
    }


def create_document(workspace: Workspace, body: dict[str, object]) -> dict[str, object]:
    response = workspace.admin.post("/api/documents", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def submit(workspace: Workspace, identifier: object) -> int:
    response = workspace.admin.post(f"/api/documents/{identifier}/actions", json={"action": "submit"})
    assert response.status_code == 200, response.text
    approvals = workspace.admin.get("/api/approvals").json()
    return next(
        value["id"]
        for value in approvals
        if value["document_id"] == identifier and value["status"] == "pending"
    )


def approve(workspace: Workspace, identifier: int) -> dict[str, object]:
    response = workspace.reviewer.post(f"/api/approvals/{identifier}/decision", json={"decision": "approved"})
    assert response.status_code == 200, response.text
    return response.json()


def invoice(workspace: Workspace, customer_id: int, amount: str = "100.00") -> dict[str, object]:
    created = create_document(workspace, payload(customer_id, amount=amount))
    return approve(workspace, submit(workspace, created["id"]))


def payment(
    workspace: Workspace,
    customer_id: int,
    amount: str,
    allocations: list[dict[str, object]],
    key: str | None = None,
) -> dict[str, object]:
    return {
        "customer_id": customer_id,
        "amount": amount,
        "date": str(date.today()),
        "reference": "Bank receipt",
        "idempotency_key": key or str(uuid4()),
        "allocations": allocations,
    }


def account_document(
    workspace: Workspace, customer_id: int, kind: str, amount: str, invoice_id: object = None
) -> dict[str, object]:
    body = {
        "kind": kind,
        "customer_id": customer_id,
        "date": str(date.today()),
        "due": str(date.today()),
        "amount": amount,
        "reason": "Recorded correction",
        "invoice_id": invoice_id,
    }
    return create_document(workspace, body)


def test_quote_to_invoice_payment_ledger_statement_and_reports(workspace: Workspace) -> None:
    customer_id = create_customer(workspace)
    body = payload(customer_id, "quote", "125.55")
    body.update(
        {
            "items": [{"description": "Consulting", "quantity": "2.5", "unit_price": "125.55"}],
            "discount_rate": "10",
            "tax_rate": "5",
        }
    )
    quote = create_document(workspace, body)
    assert quote["total"] == "296.61"
    assert workspace.admin.get(f"/api/customers/{customer_id}/ledger").json()["balance"] == "0.00"
    request = submit(workspace, quote["id"])
    assert (
        workspace.admin.post(f"/api/approvals/{request}/decision", json={"decision": "approved"}).status_code
        == 403
    )
    assert approve(workspace, request)["status"] == "approved"
    for action in ["send", "accept"]:
        assert (
            workspace.admin.post(f"/api/documents/{quote['id']}/actions", json={"action": action}).status_code
            == 200
        )
    converted = workspace.finance.post(f"/api/documents/{quote['id']}/actions", json={"action": "convert"})
    assert converted.status_code == 200, converted.text
    document = converted.json()
    assert document["total"] == "296.61"
    assert (
        workspace.finance.post(
            f"/api/documents/{quote['id']}/actions", json={"action": "convert"}
        ).status_code
        == 409
    )
    issued = approve(workspace, submit(workspace, document["id"]))
    assert issued["status"] == "issued"
    response = workspace.finance.post(
        "/api/payments",
        json=payment(workspace, customer_id, "150", [{"invoice_id": document["id"], "amount": "150"}]),
    )
    assert response.status_code == 201, response.text
    ledger = workspace.admin.get(f"/api/customers/{customer_id}/ledger").json()
    assert ledger["balance"] == "146.61"
    assert [value["balance"] for value in ledger["entries"]] == ["296.61", "146.61"]
    statement = workspace.admin.get(f"/api/customers/{customer_id}/statement")
    assert statement.content.startswith(b"%PDF-")
    assert workspace.admin.get(f"/api/documents/{quote['id']}/pdf").content.startswith(b"%PDF-")
    csv_response = workspace.admin.get(f"/api/customers/{customer_id}/statement?format=csv")
    assert "146.61" in csv_response.text
    report = workspace.finance.get("/api/reports").json()
    assert report["invoiced"] == "296.61"
    assert report["collected"] == "150.00"
    assert report["outstanding"] == "146.61"
    assert report["quote_conversion"] == 100


def test_rejection_revision_snapshots_and_approval_reset(workspace: Workspace) -> None:
    customer_id = create_customer(workspace)
    created = create_document(workspace, payload(customer_id, "quote", "200"))
    request = submit(workspace, created["id"])
    assert (
        workspace.reviewer.post(
            f"/api/approvals/{request}/decision", json={"decision": "rejected"}
        ).status_code
        == 400
    )
    rejected = workspace.reviewer.post(
        f"/api/approvals/{request}/decision", json={"decision": "rejected", "comment": "Revise the price"}
    )
    assert rejected.status_code == 200
    changed = workspace.admin.put(
        f"/api/documents/{created['id']}", json=payload(customer_id, "quote", "180")
    ).json()
    assert changed["revision"] == 2 and changed["status"] == "draft"
    assert (
        workspace.reviewer.post(
            f"/api/approvals/{request}/decision", json={"decision": "approved"}
        ).status_code
        == 409
    )
    history = workspace.admin.get(f"/api/documents/{created['id']}/revisions").json()
    assert [value["total"] for value in history] == ["200.00", "180.00"]
    old_request = workspace.admin.get("/api/approvals").json()[0]
    assert old_request["document"]["total"] == "200.00"
    assert old_request["document"]["revision"] == 1
    approve(workspace, submit(workspace, created["id"]))
    assert (
        workspace.admin.put(
            f"/api/documents/{created['id']}", json=payload(customer_id, "quote", "160")
        ).json()["status"]
        == "draft"
    )


def test_submitter_cannot_approve_another_authors_document(workspace: Workspace) -> None:
    customer_id = create_customer(workspace)
    created = workspace.sales.post("/api/documents", json=payload(customer_id, "quote")).json()
    assert (
        workspace.reviewer.post(
            f"/api/documents/{created['id']}/actions", json={"action": "submit"}
        ).status_code
        == 200
    )
    request = workspace.reviewer.get("/api/approvals").json()[0]["id"]
    assert (
        workspace.reviewer.post(
            f"/api/approvals/{request}/decision", json={"decision": "approved"}
        ).status_code
        == 403
    )
    assert (
        workspace.admin.post(f"/api/approvals/{request}/decision", json={"decision": "approved"}).status_code
        == 200
    )


def test_draft_and_rejected_documents_do_not_post_and_issued_is_immutable(workspace: Workspace) -> None:
    customer_id = create_customer(workspace)
    created = create_document(workspace, payload(customer_id))
    assert workspace.admin.get(f"/api/customers/{customer_id}/ledger").json()["entries"] == []
    request = submit(workspace, created["id"])
    assert workspace.admin.get(f"/api/customers/{customer_id}/ledger").json()["entries"] == []
    approve(workspace, request)
    assert (
        workspace.admin.put(
            f"/api/documents/{created['id']}", json=payload(customer_id, amount="500")
        ).status_code
        == 409
    )
    assert (
        workspace.admin.post(f"/api/documents/{created['id']}/actions", json={"action": "cancel"}).status_code
        == 409
    )
    assert (
        workspace.reviewer.post(
            f"/api/approvals/{request}/decision", json={"decision": "approved"}
        ).status_code
        == 409
    )
    with workspace.sessions() as db:
        assert db.scalar(select(func.count(LedgerEntry.id))) == 1


def test_credit_note_on_paid_invoice_releases_credit_for_reallocation_and_refund(
    workspace: Workspace,
) -> None:
    customer_id = create_customer(workspace)
    issued = invoice(workspace, customer_id, "100")
    receipt = workspace.finance.post(
        "/api/payments",
        json=payment(workspace, customer_id, "100", [{"invoice_id": issued["id"], "amount": "100"}]),
    ).json()
    credit = account_document(workspace, customer_id, "credit", "40", issued["id"])
    approve(workspace, submit(workspace, credit["id"]))
    account = workspace.admin.get("/api/customers").json()[0]
    assert account["balance"] == "-40.00" and account["unallocated"] == "40.00"
    assert workspace.finance.get("/api/payments").json()[0]["unallocated"] == "40.00"
    second = invoice(workspace, customer_id, "25")
    assert (
        workspace.finance.post(
            f"/api/payments/{receipt['id']}/allocate", json=[{"invoice_id": second["id"], "amount": "25"}]
        ).status_code
        == 200
    )
    refund = account_document(workspace, customer_id, "refund", "15")
    approve(workspace, submit(workspace, refund["id"]))
    account = workspace.admin.get("/api/customers").json()[0]
    assert account["balance"] == "0.00" and account["unallocated"] == "0.00"
    assert workspace.admin.get("/api/reports").json()["refunded"] == "15.00"
    assert workspace.admin.get("/api/reports").json()["outstanding"] == "0.00"


def test_refunded_credit_cannot_be_allocated_again(workspace: Workspace) -> None:
    customer_id = create_customer(workspace)
    receipt = workspace.finance.post("/api/payments", json=payment(workspace, customer_id, "200", [])).json()
    refund = account_document(workspace, customer_id, "refund", "150")
    approve(workspace, submit(workspace, refund["id"]))
    issued = invoice(workspace, customer_id, "100")
    response = workspace.finance.post(
        f"/api/payments/{receipt['id']}/allocate", json=[{"invoice_id": issued["id"], "amount": "60"}]
    )
    assert response.status_code == 409
    assert (
        workspace.finance.post(
            f"/api/payments/{receipt['id']}/allocate", json=[{"invoice_id": issued["id"], "amount": "50"}]
        ).status_code
        == 200
    )


def test_allocations_are_atomic_and_idempotent(workspace: Workspace) -> None:
    customer_id = create_customer(workspace)
    issued = invoice(workspace, customer_id)
    body = payment(workspace, customer_id, "50", [{"invoice_id": issued["id"], "amount": "60"}])
    assert workspace.finance.post("/api/payments", json=body).status_code == 400
    with workspace.sessions() as db:
        assert db.scalar(select(func.count(Payment.id))) == 0
    body = payment(workspace, customer_id, "50", [{"invoice_id": issued["id"], "amount": "50"}])
    first = workspace.finance.post("/api/payments", json=body)
    assert first.status_code == 201
    repeated = workspace.finance.post("/api/payments", json=body)
    assert repeated.status_code == 201 and repeated.json() == first.json()
    conflicting = dict(body, amount="70")
    assert workspace.finance.post("/api/payments", json=conflicting).status_code == 409
    with workspace.sessions() as db:
        assert db.scalar(select(func.count(Payment.id))) == 1


def test_allocation_rejects_cross_customer_overpayment_and_duplicates(workspace: Workspace) -> None:
    first, second = create_customer(workspace, "First"), create_customer(workspace, "Second")
    issued = invoice(workspace, first)
    assert (
        workspace.finance.post(
            "/api/payments",
            json=payment(workspace, second, "100", [{"invoice_id": issued["id"], "amount": "100"}]),
        ).status_code
        == 400
    )
    assert (
        workspace.finance.post(
            "/api/payments",
            json=payment(workspace, first, "150", [{"invoice_id": issued["id"], "amount": "150"}]),
        ).status_code
        == 409
    )
    entries = [{"invoice_id": issued["id"], "amount": "25"}] * 2
    assert (
        workspace.finance.post("/api/payments", json=payment(workspace, first, "50", entries)).status_code
        == 400
    )


def test_concurrent_allocation_and_duplicate_payment_are_serialized(workspace: Workspace) -> None:
    customer_id = create_customer(workspace)
    issued = invoice(workspace, customer_id)
    bodies = [
        payment(workspace, customer_id, "80", [{"invoice_id": issued["id"], "amount": "80"}])
        for _ in range(2)
    ]

    def record(body: dict[str, object]) -> int:
        return workspace.finance.post("/api/payments", json=body).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(record, bodies))
    assert sorted(results) == [201, 409]
    body = payment(workspace, customer_id, "20", [])
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(record, [body, body])) == [201, 201]
    with workspace.sessions() as db:
        assert db.scalar(select(func.count(Payment.id))) == 2


def test_credit_and_refund_recheck_at_approval(workspace: Workspace) -> None:
    customer_id = create_customer(workspace)
    issued = invoice(workspace, customer_id)
    first = account_document(workspace, customer_id, "credit", "70", issued["id"])
    second = account_document(workspace, customer_id, "credit", "70", issued["id"])
    first_request, second_request = submit(workspace, first["id"]), submit(workspace, second["id"])
    approve(workspace, first_request)
    assert (
        workspace.reviewer.post(
            f"/api/approvals/{second_request}/decision", json={"decision": "approved"}
        ).status_code
        == 409
    )
    workspace.finance.post("/api/payments", json=payment(workspace, customer_id, "50", []))
    refund = account_document(workspace, customer_id, "refund", "60")
    request = submit(workspace, refund["id"])
    assert (
        workspace.reviewer.post(
            f"/api/approvals/{request}/decision", json={"decision": "approved"}
        ).status_code
        == 409
    )


def test_opening_balance_adjustment_and_dated_statement(workspace: Workspace) -> None:
    customer_id = create_customer(workspace)
    opening = account_document(workspace, customer_id, "opening", "500")
    approve(workspace, submit(workspace, opening["id"]))
    adjustment = account_document(workspace, customer_id, "adjustment", "-50")
    approve(workspace, submit(workspace, adjustment["id"]))
    workspace.finance.post("/api/payments", json=payment(workspace, customer_id, "100", []))
    ledger = workspace.admin.get(f"/api/customers/{customer_id}/ledger").json()
    assert ledger["balance"] == "350.00"
    second = account_document(workspace, customer_id, "opening", "1")
    request = submit(workspace, second["id"])
    assert (
        workspace.reviewer.post(
            f"/api/approvals/{request}/decision", json={"decision": "approved"}
        ).status_code
        == 409
    )
    tomorrow = str(date.today() + timedelta(days=1))
    ledger = workspace.admin.get(f"/api/customers/{customer_id}/ledger?start={tomorrow}").json()
    assert ledger["opening"] == "350.00" and ledger["entries"] == []
    assert (
        workspace.admin.get(
            f"/api/customers/{customer_id}/ledger?start={tomorrow}&end={date.today()}"
        ).status_code
        == 400
    )


def test_tax_rounding_currency_precision_and_currency_lock(workspace: Workspace) -> None:
    config = workspace.admin.get("/api/settings").json()
    config.update(
        {"currency": "KWD", "decimals": 3, "number_prefixes": dict(config["number_prefixes"], quote="QT")}
    )
    assert workspace.admin.put("/api/settings", json=config).status_code == 200
    customer_id = create_customer(workspace)
    body = payload(customer_id, "quote", "0.001")
    body["items"] = [{"description": "Fractional unit", "quantity": "1.5", "unit_price": "0.001"}]
    created = create_document(workspace, body)
    assert created["total"] == "0.002" and str(created["number"]).startswith("QT-")
    config.update({"currency": "USD", "decimals": 2})
    assert workspace.admin.put("/api/settings", json=config).status_code == 409
    assert minor(Decimal("10.005"), 2) == 1001


def test_customer_deposit_locks_currency(workspace: Workspace) -> None:
    customer_id = create_customer(workspace)
    assert (
        workspace.finance.post("/api/payments", json=payment(workspace, customer_id, "10", [])).status_code
        == 201
    )
    config = workspace.admin.get("/api/settings").json()
    config["currency"] = "EUR"
    assert workspace.admin.put("/api/settings", json=config).status_code == 409


def test_quote_approval_rules_and_expiry(workspace: Workspace) -> None:
    config = workspace.admin.get("/api/settings").json()
    config.update({"approval_threshold": "1000", "discount_threshold": "5"})
    assert workspace.admin.put("/api/settings", json=config).status_code == 200
    customer_id = create_customer(workspace)
    created = create_document(workspace, payload(customer_id, "quote"))
    response = workspace.admin.post(f"/api/documents/{created['id']}/actions", json={"action": "submit"})
    assert response.json()["status"] == "approved"
    body = payload(customer_id, "quote")
    body["discount_rate"] = "6"
    discounted = create_document(workspace, body)
    assert submit(workspace, discounted["id"])
    expired = payload(customer_id, "quote")
    expired.update(
        {"date": str(date.today() - timedelta(days=2)), "due": str(date.today() - timedelta(days=1))}
    )
    quote = create_document(workspace, expired)
    for action in ["submit", "send"]:
        assert (
            workspace.admin.post(f"/api/documents/{quote['id']}/actions", json={"action": action}).status_code
            == 200
        )
    assert (
        workspace.admin.post(f"/api/documents/{quote['id']}/actions", json={"action": "accept"}).status_code
        == 409
    )


def test_cancellation_invalidates_pending_approval(workspace: Workspace) -> None:
    created = create_document(workspace, payload(create_customer(workspace)))
    request = submit(workspace, created["id"])
    assert (
        workspace.admin.post(f"/api/documents/{created['id']}/actions", json={"action": "cancel"}).status_code
        == 200
    )
    assert (
        workspace.reviewer.post(
            f"/api/approvals/{request}/decision", json={"decision": "approved"}
        ).status_code
        == 409
    )


def test_public_origin_supports_proxy_login_and_writes(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    public_origin = "https://crm.example.test"
    credentials = {"email": "admin@crm.local", "password": "IntegrationTest!2026"}
    with TestClient(main.app) as client:
        assert (
            client.post("/api/auth/login", json=credentials, headers={"origin": public_origin}).status_code
            == 403
        )
        assert (
            client.post(
                "/api/auth/login", json=credentials, headers={"origin": "http://testserver"}
            ).status_code
            == 200
        )
        monkeypatch.setenv("CRM_PUBLIC_ORIGIN", public_origin)
        assert (
            client.post("/api/auth/login", json=credentials, headers={"origin": public_origin}).status_code
            == 200
        )
        assert client.get("/api/auth/me").status_code == 200
        assert (
            client.post(
                "/api/customers", json={"name": "Preview customer"}, headers={"origin": public_origin}
            ).status_code
            == 201
        )
        for origin in ["https://untrusted.example", "http://crm.example.test", "http://testserver"]:
            assert (
                client.post(
                    "/api/customers",
                    json={"name": "Blocked"},
                    headers={"origin": origin, "x-forwarded-host": "crm.example.test"},
                ).status_code
                == 403
            )
        assert (
            client.post(
                "/api/customers",
                json={"name": "Blocked"},
                headers={"origin": public_origin, "sec-fetch-site": "cross-site"},
            ).status_code
            == 403
        )
        assert client.post("/api/auth/logout", headers={"origin": public_origin}).status_code == 200
        assert client.get("/api/auth/me").status_code == 401


def test_role_permissions_sessions_and_cross_origin_protection(workspace: Workspace) -> None:
    customer_id = create_customer(workspace)
    assert workspace.sales.post("/api/documents", json=payload(customer_id)).status_code == 403
    assert workspace.sales.get("/api/reports").status_code == 403
    assert workspace.finance.post("/api/customers", json={"name": "Blocked"}).status_code == 403
    assert (
        workspace.sales.post(
            "/api/users",
            json={"name": "Blocked", "email": "x@y.com", "password": "LongPassword!2026", "role": "admin"},
        ).status_code
        == 403
    )
    assert (
        workspace.admin.post(
            "/api/customers", json={"name": "Blocked"}, headers={"origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    assert (
        workspace.admin.post(
            "/api/customers", json={"name": "Blocked"}, headers={"sec-fetch-site": "cross-site"}
        ).status_code
        == 403
    )
    with TestClient(main.app) as anonymous:
        assert anonymous.get("/api/customers").status_code == 401
    member = workspace.sales.get("/api/auth/me").json()
    assert workspace.admin.post(f"/api/users/{member['id']}/toggle").status_code == 200
    assert workspace.sales.get("/api/auth/me").status_code == 401
    assert workspace.admin.post("/api/auth/logout").status_code == 200
    assert workspace.admin.get("/api/auth/me").status_code == 401


def test_customer_contacts_addresses_csv_and_attachment_safety(workspace: Workspace) -> None:
    response = workspace.admin.post(
        "/api/customers",
        json={
            "name": "=HYPERLINK example",
            "email": "one@example.test",
            "contacts": [{"name": "Alex", "email": "alex@example.test"}],
            "addresses": [{"label": "Shipping", "address": "42 Example Street"}],
        },
    )
    assert response.status_code == 201
    customer_id = response.json()["id"]
    exported = workspace.admin.get("/api/exports/customers")
    rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
    assert rows[1][0].startswith("'=HYPERLINK")
    assert (
        workspace.admin.post(
            "/api/customers", json={"name": "Duplicate", "email": "ONE@EXAMPLE.TEST"}
        ).status_code
        == 409
    )
    uploaded = workspace.admin.post(
        f"/api/customers/{customer_id}/attachments",
        files={"file": ("../../invoice.pdf", b"%PDF-test", "application/pdf")},
    )
    assert uploaded.status_code == 201 and uploaded.json()["name"] == "invoice.pdf"
    attachment_id = uploaded.json()["id"]
    assert workspace.admin.get(f"/api/attachments/{attachment_id}").content == b"%PDF-test"
    assert (
        workspace.admin.post(
            f"/api/customers/{customer_id}/attachments", files={"file": ("malicious.html", b"<script />")}
        ).status_code
        == 400
    )
    assert (
        workspace.admin.post(
            f"/api/customers/{customer_id}/attachments",
            files={"file": ("large.pdf", b"x" * (10 * 1024 * 1024 + 1))},
        ).status_code
        == 413
    )
    imported = workspace.admin.post(
        "/api/imports/customers",
        files={
            "file": (
                "import.csv",
                b"name,email\nNew Company,new@example.test\nSecond Company,second@example.test\n",
                "text/csv",
            )
        },
    )
    assert imported.status_code == 200 and imported.json()["imported"] == 2
    failed = workspace.admin.post(
        "/api/imports/customers",
        files={
            "file": (
                "import.csv",
                b"name,email\nGood Row,good@example.test\nDuplicate,one@example.test\n",
                "text/csv",
            )
        },
    )
    assert failed.status_code == 409
    assert len(workspace.admin.get("/api/customers").json()) == 3
    assert len(workspace.admin.get(f"/api/customers/{customer_id}/history").json()["attachments"]) == 1


def test_reports_separate_period_sales_and_current_receivables(workspace: Workspace) -> None:
    customer_id = create_customer(workspace)
    body = payload(customer_id)
    body.update(
        {"date": str(date.today() - timedelta(days=50)), "due": str(date.today() - timedelta(days=35))}
    )
    created = create_document(workspace, body)
    approve(workspace, submit(workspace, created["id"]))
    report = workspace.admin.get(f"/api/reports?start={date.today()}&end={date.today()}").json()
    assert report["invoiced"] == "0.00" and report["outstanding"] == "100.00"
    assert report["ageing"] == ["0.00", "0.00", "100.00", "0.00", "0.00"]


@pytest.mark.parametrize(
    "change",
    [
        {"items": []},
        {"due": "2000-01-01"},
        {"tax_rate": "101"},
        {"items": [{"description": "Bad", "quantity": "-1", "unit_price": "100"}]},
        {"items": [{"description": "Bad", "quantity": "1", "unit_price": "NaN"}]},
        {"unknown_field": "rejected"},
    ],
)
def test_invalid_financial_inputs_do_not_create_documents(
    workspace: Workspace, change: dict[str, object]
) -> None:
    body = payload(create_customer(workspace))
    body.update(change)
    assert workspace.admin.post("/api/documents", json=body).status_code in {400, 422}
    assert workspace.admin.get("/api/documents").json() == []


def test_demo_database_is_refused_in_production(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workspace.sessions() as db:
        config = db.get(Settings, 1)
        assert config is not None
        config.is_demo = True
        db.commit()
    monkeypatch.setattr(main, "PRODUCTION", True)
    with pytest.raises(RuntimeError, match="demo workspaces"):
        with TestClient(main.app):
            pass
