import json
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from conftest import Workspace
from test_workflows import (
    account_document,
    approve,
    create_customer,
    create_document,
    invoice,
    payload,
    payment,
    submit,
)

from app import erp
from app.models import Settings


@dataclass
class Remote:
    tenant: str = field(default_factory=lambda: str(uuid4()))
    company: str = field(default_factory=lambda: str(uuid4()))
    account_ids: dict[str, str] = field(
        default_factory=lambda: {
            key: str(uuid4())
            for key in ["receivable", "revenue", "receipts", "returns", "opening", "adjustments", "tax"]
        }
    )
    postings: dict[str, dict] = field(default_factory=dict)
    lose_acknowledgement: bool = False

    def respond(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-credential"
        if request.url.path.endswith("/connection"):
            return httpx.Response(
                200, json={"tenantId": self.tenant, "companyId": self.company, "currency": "KWD"}
            )
        if request.url.path.endswith("/accounts"):
            types = {
                "receivable": "ASSET",
                "revenue": "REVENUE",
                "receipts": "ASSET",
                "returns": "REVENUE",
                "opening": "EQUITY",
                "adjustments": "EXPENSE",
                "tax": "LIABILITY",
            }
            return httpx.Response(
                200,
                json=[
                    {"id": identifier, "type": types[key], "isPostable": True, "isActive": True}
                    for key, identifier in self.account_ids.items()
                ],
            )
        assert request.method == "POST"
        body = json.loads(request.content)
        key = body["externalId"]
        if key in self.postings:
            assert self.postings[key]["payload"] == body
            return httpx.Response(200, json=self.postings[key]["result"])
        result = {"id": str(uuid4()), "number": len(self.postings) + 1}
        self.postings[key] = {"payload": body, "result": result}
        if self.lose_acknowledgement:
            self.lose_acknowledgement = False
            raise httpx.ReadTimeout("Response lost", request=request)
        return httpx.Response(201, json=result)


@pytest.fixture
def remote(workspace: Workspace, monkeypatch: pytest.MonkeyPatch) -> Remote:
    with workspace.sessions.begin() as session:
        config = session.get(Settings, 1)
        assert config is not None
        config.currency, config.decimals = "KWD", 3
    monkeypatch.setenv("CRM_ERP_URL", "https://erp.example.test")
    monkeypatch.setenv("CRM_ERP_TOKEN", "test-credential")
    monkeypatch.setenv("CRM_ERP_SYNC_ENABLED", "true")
    remote = Remote()
    client = httpx.Client
    monkeypatch.setattr(
        erp.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(remote.respond))
    )
    return remote


def activate(workspace: Workspace, remote: Remote) -> dict:
    status = workspace.admin.get("/api/accounting/sync-status").json()
    return {
        "tenant_id": remote.tenant,
        "company_id": remote.company,
        "cutoff_ledger_id": status["cutoff_ledger_id"],
        "account_ids": remote.account_ids,
        "reconciled": True,
    }


def test_history_is_not_reposted_and_only_an_admin_can_activate(workspace: Workspace, remote: Remote) -> None:
    customer = create_customer(workspace)
    invoice(workspace, customer)
    body = activate(workspace, remote)
    assert workspace.finance.post("/api/accounting/activate", json=body).status_code == 403
    assert workspace.admin.post("/api/accounting/activate", json=body).status_code == 200
    assert workspace.finance.post("/api/accounting/sync").json()["posted"] == 0
    assert not remote.postings
    invoice(workspace, customer, "25.501")
    synced = workspace.finance.post("/api/accounting/sync").json()
    assert synced["posted"] == 1
    assert synced["pending"] == 0
    assert synced["historical_count"] == 1
    assert len(synced["postings"]) == 1
    assert workspace.finance.post("/api/accounting/sync").json()["posted"] == 0
    assert len(remote.postings) == 1


def test_lost_acknowledgement_retries_the_same_immutable_event(workspace: Workspace, remote: Remote) -> None:
    assert (
        workspace.admin.post("/api/accounting/activate", json=activate(workspace, remote)).status_code == 200
    )
    invoice(workspace, create_customer(workspace))
    remote.lose_acknowledgement = True
    result = workspace.finance.post("/api/accounting/sync").json()
    assert result["posted"] == 0 and result["failure"]
    assert len(remote.postings) == 1
    result = workspace.finance.post("/api/accounting/sync").json()
    assert result["posted"] == 1 and result["failure"] is None
    assert len(remote.postings) == 1


def test_all_financial_events_balance_and_partial_credit_tax_rounds_exactly(
    workspace: Workspace, remote: Remote
) -> None:
    assert (
        workspace.admin.post("/api/accounting/activate", json=activate(workspace, remote)).status_code == 200
    )
    customer = create_customer(workspace)
    body = payload(customer, amount="100.001")
    body["tax_rate"] = "5"
    created = create_document(workspace, body)
    issued = approve(workspace, submit(workspace, created["id"]))
    response = workspace.finance.post("/api/payments", json=payment(workspace, customer, "105.001", []))
    assert response.status_code == 201
    for amount in ["35.000", "35.000", "35.001"]:
        credit = account_document(workspace, customer, "credit", amount, issued["id"])
        approve(workspace, submit(workspace, credit["id"]))
    refund = account_document(workspace, customer, "refund", "40.000")
    approve(workspace, submit(workspace, refund["id"]))
    other = create_customer(workspace, "Another")
    for kind, amount in [("opening", "12.000"), ("adjustment", "-2.000")]:
        document = account_document(workspace, other, kind, amount)
        approve(workspace, submit(workspace, document["id"]))
    response = workspace.finance.post("/api/accounting/sync").json()
    assert response["posted"] == 8, response
    credits = []
    for event in remote.postings.values():
        journal = event["payload"]
        assert sum(Decimal(line["debit"]) for line in journal["lines"]) == sum(
            Decimal(line["credit"]) for line in journal["lines"]
        )
        credits.extend(
            Decimal(line["debit"])
            for line in journal["lines"]
            if line["accountId"] == remote.account_ids["tax"]
        )
    assert credits == [Decimal("0"), Decimal("1.667"), Decimal("1.666"), Decimal("1.667")]


def test_cutoff_and_company_mismatch_are_rejected(workspace: Workspace, remote: Remote) -> None:
    body = activate(workspace, remote)
    invoice(workspace, create_customer(workspace))
    assert workspace.admin.post("/api/accounting/activate", json=body).status_code == 409
    assert (
        workspace.admin.post("/api/accounting/activate", json=activate(workspace, remote)).status_code == 200
    )
    remote.company = str(uuid4())
    assert workspace.finance.get("/api/accounting/accounts").status_code == 409
    assert workspace.finance.post("/api/accounting/sync").status_code == 409
    assert not remote.postings


def test_mapping_validation_and_posting_gate(
    workspace: Workspace, remote: Remote, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = activate(workspace, remote)
    body["account_ids"] = {**remote.account_ids, "receivable": remote.account_ids["revenue"]}
    assert workspace.admin.post("/api/accounting/activate", json=body).status_code == 400
    assert (
        workspace.admin.post("/api/accounting/activate", json=activate(workspace, remote)).status_code == 200
    )
    invoice(workspace, create_customer(workspace))
    monkeypatch.setenv("CRM_ERP_SYNC_ENABLED", "false")
    assert workspace.finance.post("/api/accounting/sync").status_code == 409
    assert not remote.postings
