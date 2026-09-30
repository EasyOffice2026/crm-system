from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import main
from app.db import db_session

PASSWORD = "IntegrationTest!2026"


@dataclass
class Workspace:
    admin: TestClient
    reviewer: TestClient
    sales: TestClient
    finance: TestClient
    sessions: sessionmaker[Session]
    engine: Engine


def sign_in(client: TestClient, email: str = "admin@crm.local") -> None:
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Workspace]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setenv("CRM_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(main, "UPLOADS", tmp_path / "uploads")
    main.failed_logins.clear()

    def database() -> Iterator[Session]:
        with sessions() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    main.app.dependency_overrides[db_session] = database
    with (
        TestClient(main.app) as admin,
        TestClient(main.app) as reviewer,
        TestClient(main.app) as sales,
        TestClient(main.app) as finance,
    ):
        sign_in(admin)
        for name, role, client in [
            ("Reviewer", "manager", reviewer),
            ("Sales", "sales", sales),
            ("Finance", "finance", finance),
        ]:
            response = admin.post(
                "/api/users",
                json={"name": name, "email": f"{role}@crm.local", "password": PASSWORD, "role": role},
            )
            assert response.status_code == 201
            sign_in(client, f"{role}@crm.local")
        yield Workspace(admin, reviewer, sales, finance, sessions, engine)
    main.app.dependency_overrides.clear()
    engine.dispose()
