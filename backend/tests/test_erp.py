import httpx
import pytest

from app import erp
from app.models import Settings


def test_erp_connection_is_disabled_without_credentials(workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRM_ERP_URL", raising=False)
    monkeypatch.delenv("CRM_ERP_TOKEN", raising=False)
    assert workspace.admin.get("/api/accounting/connection").status_code == 503
    assert workspace.sales.get("/api/accounting/connection").status_code == 403


def test_erp_connection_scopes_all_requests_to_token(workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    with workspace.sessions.begin() as session:
        config = session.get(Settings, 1)
        assert config is not None
        config.currency, config.decimals = "KWD", 3
    monkeypatch.setenv("CRM_ERP_URL", "https://erp.example.test")
    monkeypatch.setenv("CRM_ERP_TOKEN", "private-token")
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer private-token"
        assert request.url.host == "erp.example.test"
        requests.append(request.url.path)
        if request.url.path.endswith("/connection"):
            return httpx.Response(
                200, json={"tenantName": "Group", "companyName": "Company", "currency": "KWD"}
            )
        if request.url.path.endswith("/trial-balance"):
            assert request.url.params["from"] == "2026-01-01"
            assert request.url.params["to"] == "2026-12-31"
        return httpx.Response(200, json=[])

    real_client = httpx.Client
    monkeypatch.setattr(
        erp.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(respond))
    )
    assert workspace.finance.get("/api/accounting/connection").json()["companyName"] == "Company"
    assert workspace.finance.get("/api/accounting/accounts").json() == []
    assert workspace.finance.get("/api/accounting/journals").status_code == 200
    assert (
        workspace.finance.get("/api/accounting/trial-balance?start=2026-01-01&end=2026-12-31").status_code
        == 200
    )
    assert workspace.sales.get("/api/accounting/accounts").status_code == 403
    assert len(requests) == 7


def test_erp_rejects_invalid_origin_and_currency(workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRM_ERP_TOKEN", "private-token")
    monkeypatch.setenv("CRM_ERP_URL", "http://remote.example.test")
    assert workspace.admin.get("/api/accounting/connection").status_code == 503
    monkeypatch.setenv("CRM_ERP_URL", "https://erp.example.test")
    real_client = httpx.Client
    monkeypatch.setattr(
        erp.httpx,
        "Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"currency": "USD"}))
        ),
    )
    assert workspace.admin.get("/api/accounting/connection").status_code == 409
