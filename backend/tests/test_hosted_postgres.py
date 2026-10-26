import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import func, select
from test_hosted import (
    PLATFORM_KEY,
    REVIEWERS,
    FakeErp,
    browser,
    control_workspace,
    issue_invoice,
    login,
    outbox_rows,
    provision,
)

from app import accounting_worker, main, migrate
from app.control import WorkspaceCredential, control_engine, control_sessions, reset_control
from app.tenancy import dispose_all

POSTGRES = os.environ.get("CRM_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES, reason="Set CRM_TEST_POSTGRES_URL to a disposable superuser DSN"
)


def admin_dsn() -> str:
    return POSTGRES.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture
def pg_erp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeErp]:
    base = POSTGRES.rsplit("/", 1)[0]
    values = {
        "CRM_TENANCY": "hosted",
        "CRM_CONTROL_DATABASE_URL": POSTGRES,
        "CRM_WORKSPACE_DATABASE_URL": f"{base}/postgres",
        "CRM_PROVISIONER_DATABASE_URL": POSTGRES,
        "CRM_BASE_DOMAIN": "crm.test",
        "CRM_SECRET_KEY": "p" * 40,
        "CRM_UPLOAD_DIR": str(tmp_path / "uploads"),
        "CRM_ERP_URL": "https://erp.test",
        "CRM_ERP_PLATFORM_KEY": PLATFORM_KEY,
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CRM_ADMIN_PASSWORD", raising=False)
    reset_control()
    dispose_all()
    with psycopg.connect(admin_dsn(), autocommit=True) as connection:
        connection.execute(
            "DROP TABLE IF EXISTS crm_provider_audit, crm_workspace_credentials, crm_workspaces, alembic_version"
        )
    migrate.upgrade(control_engine(), "control")
    main.failed_logins.clear()
    yield FakeErp()
    for client in REVIEWERS.values():
        client.__exit__(None, None, None)
    REVIEWERS.clear()
    dispose_all()
    reset_control()
    with psycopg.connect(admin_dsn(), autocommit=True) as connection:
        names = [
            row[0]
            for row in connection.execute("SELECT datname FROM pg_database WHERE datname LIKE 'crm_ws_%'")
        ]
        for name in names:
            connection.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
            connection.execute(f'DROP ROLE IF EXISTS "{name}"')


def test_postgres_provisioning_isolation_and_concurrent_workers(pg_erp: FakeErp) -> None:
    provision(pg_erp, "pgone")
    provision(pg_erp, "pgtwo")
    workspace = control_workspace("pgone")
    with psycopg.connect(admin_dsn(), autocommit=True) as connection:
        owner = connection.execute(
            "SELECT pg_get_userbyid(datdba), has_database_privilege('public', datname, 'CONNECT') "
            "FROM pg_database WHERE datname=%s",
            (workspace.db_name,),
        ).fetchone()
    assert owner == (workspace.db_role, False)
    with control_sessions()() as control:
        assert control.scalar(select(func.count(WorkspaceCredential.id))) == 2
    with browser("pgone") as one, browser("pgtwo") as two:
        login(one, "pgone")
        login(two, "pgtwo")
        first = one.post("/api/customers", json={"name": "One"}).json()["id"]
        second = two.post("/api/customers", json={"name": "Two"}).json()["id"]
        assert first == second == 1
        for _ in range(4):
            issue_invoice(one, first)
        issue_invoice(two, second)
        stolen = one.cookies.get("crm_session")
        assert stolen
        two.cookies.set("crm_session", stolen)
        assert two.get("/api/auth/me").status_code == 401
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda name: accounting_worker.run_once(pg_erp.client(), name), ["a", "b", "c", "d"]))
    assert [row.status for row in outbox_rows("pgone")] == ["posted"] * 4
    assert [row.status for row in outbox_rows("pgtwo")] == ["posted"]
    assert len(pg_erp.journals) == 5
