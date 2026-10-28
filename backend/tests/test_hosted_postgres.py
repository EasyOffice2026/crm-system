import os
import secrets
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import func, inspect, select
from sqlalchemy.engine import make_url
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
    request,
)

from app import accounting_worker, main, migrate, provider_worker
from app.control import Workspace, WorkspaceCredential, control_engine, control_sessions, reset_control
from app.provisioning import ErpPlatform, record_request
from app.tenancy import dispose_all

POSTGRES = os.environ.get("CRM_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES, reason="Set CRM_TEST_POSTGRES_URL to a disposable superuser DSN"
)


def admin_dsn() -> str:
    return POSTGRES.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture
def pg_erp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeErp]:
    server = make_url(POSTGRES)
    if server.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("CRM_TEST_POSTGRES_URL must point to a disposable loopback PostgreSQL server")
    name = f"crm_test_{uuid.uuid4().hex}"
    provisioner = f"crm_provisioner_{uuid.uuid4().hex}"
    password = secrets.token_urlsafe(32)
    control_url = server.set(database=name).render_as_string(hide_password=False)
    values = {
        "CRM_TENANCY": "hosted",
        "CRM_CONTROL_DATABASE_URL": control_url,
        "CRM_WORKSPACE_DATABASE_URL": server.set(database="postgres").render_as_string(hide_password=False),
        "CRM_PROVISIONER_DATABASE_URL": server.set(username=provisioner, password=password).render_as_string(
            hide_password=False
        ),
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
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN CREATEROLE CREATEDB PASSWORD {}").format(
                sql.Identifier(provisioner), sql.Literal(password)
            )
        )
    try:
        migrate.upgrade(control_engine(), "control")
        main.failed_logins.clear()
        yield FakeErp()
    finally:
        for client in REVIEWERS.values():
            client.__exit__(None, None, None)
        REVIEWERS.clear()
        names = []
        if inspect(control_engine()).has_table(Workspace.__tablename__):
            with control_sessions()() as control:
                names = list(control.scalars(select(Workspace.db_name)))
        dispose_all()
        reset_control()
        with psycopg.connect(admin_dsn(), autocommit=True) as connection:
            for database in [*names, name]:
                connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
            for role in names:
                connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(provisioner)))


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


def test_postgres_provider_workers_share_durable_schedule(pg_erp: FakeErp) -> None:
    record_request(request("pgqueued"), "test")
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: provider_worker.run_once(ErpPlatform(pg_erp.client())), range(4)))
    workspace = control_workspace("pgqueued")
    assert workspace.status == "active" and workspace.provision_attempts == 1
    assert workspace.next_provision_attempt_at is None
    assert len(pg_erp.bindings) == len(pg_erp.credentials) == 1
