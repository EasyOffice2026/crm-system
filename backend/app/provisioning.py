import hashlib
import os
import re
import secrets
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import migrate
from .control import (
    RESERVED_SLUGS,
    SLUG_PATTERN,
    Workspace,
    WorkspaceCredential,
    control_sessions,
    provider_audit,
)
from .erp import erp_base
from .models import AccountingState, Invite, Settings, User
from .outbox import canonical_json
from .secretbox import decrypt, encrypt, sha256_hex
from .security import password_hash
from .tenancy import (
    dispose_workspace,
    make_workspace_engine,
    upload_base,
    workspace_database_url,
    workspace_origin,
)

STEPS = (
    "control_row",
    "database",
    "migrate",
    "settings",
    "erp_binding",
    "credential",
    "verify",
    "admin",
    "active",
)
INVITE_HOURS = 72


class ProvisionError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class ProvisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: uuid.UUID
    slug: str = Field(min_length=2, max_length=41)
    company_name: str = Field(min_length=1, max_length=200)
    company_name_ar: str = Field(default="", max_length=200)
    legal_name: str = Field(min_length=1, max_length=200)
    cr_number: str = Field(min_length=1, max_length=40)
    fy_start: date
    fiscal_year_end_month: int = Field(default=12, ge=1, le=12)
    admin_email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    admin_name: str = Field(default="Workspace Admin", min_length=1, max_length=120)

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value) or value in RESERVED_SLUGS or value.endswith("-"):
            raise ValueError("Slug must be 2-41 lowercase letters, digits or hyphens and not reserved")
        return value

    def body(self) -> dict[str, object]:
        data = self.model_dump(mode="json")
        data["admin_email"] = str(data["admin_email"]).lower()
        return data


def request_hash(body: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def erp_binding_body(body: dict[str, Any]) -> dict[str, object]:
    return {
        "tenant": {"slug": f"crm-{body['slug']}", "name": body["company_name"]},
        "company": {
            "legalName": body["legal_name"],
            "name": {"en": body["company_name"], "ar": body.get("company_name_ar") or body["company_name"]},
            "crNumber": body["cr_number"],
            "fiscalYearEndMonth": body.get("fiscal_year_end_month", 12),
            "firstFiscalYearStart": body["fy_start"],
        },
        "currency": "KWD",
        "chartTemplate": "general_business_v1",
    }


class ErpPlatform:
    """Server-to-server ERP calls for provisioning; keys and tokens never leave this process."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=10, follow_redirects=False)

    def base(self) -> str:
        try:
            return erp_base()
        except Exception as exc:
            raise ProvisionError(
                503, "ERP_NOT_CONFIGURED", "CRM_ERP_URL must be a trusted HTTPS origin"
            ) from exc

    def platform_headers(self) -> dict[str, str]:
        key = os.environ.get("CRM_ERP_PLATFORM_KEY", "")
        if not key:
            raise ProvisionError(503, "ERP_NOT_CONFIGURED", "CRM_ERP_PLATFORM_KEY is not configured")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def call(
        self, method: str, path: str, headers: dict[str, str], body: object | None = None
    ) -> httpx.Response:
        try:
            return self.client.request(
                method,
                f"{self.base()}{path}",
                content=canonical_json(body).encode() if body is not None else None,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise ProvisionError(502, "ERP_UNREACHABLE", f"ERP request failed: {type(exc).__name__}") from exc

    def platform(self, method: str, path: str, body: object | None = None) -> httpx.Response:
        return self.call(method, f"/api/v1/platform{path}", self.platform_headers(), body)

    def connection(self, workspace_id: str, token: str) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}", "X-Crm-Workspace-Id": workspace_id}
        return self.call("GET", "/api/v1/integrations/crm/connection", headers)


def erp_failure(response: httpx.Response, action: str) -> ProvisionError:
    code = f"HTTP_{response.status_code}"
    message = f"ERP {action} failed with HTTP {response.status_code}"
    try:
        error = response.json().get("error", {})
        code = str(error.get("code") or code)[:60]
        message = f"ERP {action} failed: {str(error.get('message') or code)[:300]}"
    except (ValueError, AttributeError):
        pass
    return ProvisionError(409 if response.status_code == 409 else 502, code, message)


def json_body(response: httpx.Response, action: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise ProvisionError(502, "ERP_BAD_RESPONSE", f"ERP {action} returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise ProvisionError(502, "ERP_BAD_RESPONSE", f"ERP {action} returned an invalid body")
    return body


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


@contextmanager
def workspace_lock(workspace_id: str) -> Iterator[None]:
    with _locks_guard:
        local = _locks.setdefault(workspace_id, threading.Lock())
    with local:
        engine = control_sessions().kw["bind"]
        if engine.dialect.name != "postgresql":
            yield
            return
        key = int.from_bytes(hashlib.sha256(workspace_id.encode()).digest()[:8], "big", signed=True)
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT pg_advisory_lock(%s)", (key,))
            try:
                yield
            finally:
                connection.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (key,))
                connection.commit()


def public_state(workspace: Workspace) -> dict[str, object]:
    return {
        "workspaceId": workspace.id,
        "slug": workspace.slug,
        "status": workspace.status,
        "step": workspace.provision_step,
        "lastError": workspace.last_error,
        "origin": workspace_origin(workspace.slug) if os.environ.get("CRM_BASE_DOMAIN") else None,
    }


def record_request(request: ProvisionRequest, actor: str) -> Workspace:
    """Step 1: create or match the control row for this request id (idempotent, conflict-safe)."""
    body = request.body()
    digest = request_hash(body)
    sessions = control_sessions()
    for _ in range(2):
        with sessions() as control:
            existing = control.scalar(
                select(Workspace).where(Workspace.provision_request_id == str(request.request_id))
            ) or control.scalar(select(Workspace).where(Workspace.slug == request.slug))
            if existing and existing.provision_request_id == str(request.request_id):
                if existing.request_hash != digest:
                    raise ProvisionError(
                        409, "WORKSPACE_REQUEST_CONFLICT", "This request id was used with a different body"
                    )
                return existing
            if existing:
                raise ProvisionError(409, "SLUG_TAKEN", "This workspace slug is already in use")
            identifier = str(uuid.uuid4())
            name = f"crm_ws_{uuid.UUID(identifier).hex}"
            workspace = Workspace(
                id=identifier,
                slug=request.slug,
                display_name=request.company_name,
                status="provisioning",
                provision_request_id=str(request.request_id),
                request_hash=digest,
                request_body=body,
                provision_step="control_row",
                db_name=name,
                db_role=name,
                db_password_enc=encrypt(secrets.token_urlsafe(32)),
                upload_root=str((upload_base() / identifier).resolve()),
            )
            control.add(workspace)
            provider_audit(control, identifier, actor, "workspace.requested", slug=request.slug)
            try:
                control.commit()
                return workspace
            except IntegrityError:
                control.rollback()
    raise ProvisionError(409, "SLUG_TAKEN", "This workspace slug is already in use")


def provisioner_dsn() -> str | None:
    url = os.environ.get("CRM_PROVISIONER_DATABASE_URL", "")
    if not url:
        return None
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def ensure_database(workspace: Workspace) -> None:
    target = os.environ.get("CRM_WORKSPACE_DATABASE_URL", "")
    if target.startswith("sqlite"):
        Path(make_url(target).database or ".").mkdir(parents=True, exist_ok=True)
        return
    dsn = provisioner_dsn()
    if not dsn:
        raise ProvisionError(503, "PROVISIONER_NOT_CONFIGURED", "CRM_PROVISIONER_DATABASE_URL is required")
    import psycopg
    from psycopg import sql

    if not re.fullmatch(r"crm_ws_[0-9a-f]{32}", workspace.db_name):
        raise ProvisionError(500, "INVALID_DB_NAME", "Workspace database name is not valid")
    role, database, password = (
        sql.Identifier(workspace.db_role),
        sql.Identifier(workspace.db_name),
        sql.Literal(decrypt(workspace.db_password_enc)),
    )
    with psycopg.connect(dsn, autocommit=True) as connection:
        if connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (workspace.db_role,)).fetchone():
            connection.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(role, password))
        else:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(role, password))
        superuser = connection.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        if not (superuser and superuser[0]):
            connection.execute(sql.SQL("GRANT {} TO current_user").format(role))
        if not connection.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (workspace.db_name,)
        ).fetchone():
            connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(database, role))
        connection.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(database))
        connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, role))


def workspace_session(workspace: Workspace) -> Session:
    engine = make_workspace_engine(workspace_database_url(workspace))
    session = Session(bind=engine, expire_on_commit=False)
    session.info["engine"] = engine
    return session


@contextmanager
def workspace_db(workspace: Workspace) -> Iterator[Session]:
    session = workspace_session(workspace)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        session.info["engine"].dispose()


def run_migrations(workspace: Workspace) -> None:
    engine = make_workspace_engine(workspace_database_url(workspace))
    try:
        migrate.upgrade(engine)
    finally:
        engine.dispose()
    root = Path(workspace.upload_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)


def seed_settings(workspace: Workspace) -> None:
    with workspace_db(workspace) as db:
        if not db.get(Settings, 1):
            db.add(Settings(id=1, company=workspace.display_name, currency="KWD", decimals=3, is_demo=False))


def bind_erp(workspace: Workspace, erp: ErpPlatform) -> dict[str, Any]:
    response = erp.platform(
        "PUT", f"/crm-workspaces/{workspace.id}", erp_binding_body(workspace.request_body)
    )
    if response.status_code not in {200, 201}:
        raise erp_failure(response, "workspace binding")
    body = json_body(response, "workspace binding")
    try:
        ids = {key: str(uuid.UUID(str(body[key]))) for key in ("bindingId", "tenantId", "companyId")}
    except (KeyError, ValueError) as exc:
        raise ProvisionError(502, "ERP_BAD_RESPONSE", "ERP workspace binding returned invalid ids") from exc
    if body.get("workspaceId") != workspace.id or body.get("currency") != "KWD":
        raise ProvisionError(502, "ERP_BAD_RESPONSE", "ERP workspace binding does not match this workspace")
    return ids


def register_credential(
    control: Session, workspace: Workspace, erp: ErpPlatform, status: str = "pending"
) -> WorkspaceCredential:
    credential = control.scalar(
        select(WorkspaceCredential)
        .where(WorkspaceCredential.workspace_id == workspace.id, WorkspaceCredential.status == status)
        .order_by(WorkspaceCredential.created_at)
    )
    if credential is None:
        credential = WorkspaceCredential(
            id=str(uuid.uuid4()),
            workspace_id=workspace.id,
            token_enc=encrypt("nxc_" + secrets.token_urlsafe(32)),
            status="pending",
        )
        control.add(credential)
        control.commit()
    response = erp.platform(
        "PUT",
        f"/crm-workspaces/{workspace.id}/credentials/{credential.id}",
        {"tokenSha256": sha256_hex(decrypt(credential.token_enc))},
    )
    if response.status_code not in {200, 201}:
        raise erp_failure(response, "credential registration")
    return credential


def verify_connection(workspace: Workspace, token: str, erp: ErpPlatform) -> dict[str, Any]:
    response = erp.connection(workspace.id, token)
    if response.status_code != 200:
        raise erp_failure(response, "connection check")
    body = json_body(response, "connection check")
    expected = {
        "workspaceId": workspace.id,
        "bindingId": workspace.erp_binding_id,
        "tenantId": workspace.erp_tenant_id,
        "companyId": workspace.erp_company_id,
        "currency": "KWD",
    }
    if any(body.get(key) != value for key, value in expected.items()):
        raise ProvisionError(
            502, "ERP_CONNECTION_MISMATCH", "ERP connection does not match the workspace binding"
        )
    return body


def write_accounting_state(workspace: Workspace, connection: dict[str, Any]) -> None:
    with workspace_db(workspace) as db:
        state = db.get(AccountingState, 1)
        if state is None:
            state = AccountingState(id=1, cutoff_ledger_id=0, activated_at=datetime.now(UTC))
            db.add(state)
        state.workspace_id = workspace.id
        state.binding_id = workspace.erp_binding_id
        state.erp_tenant_id = workspace.erp_tenant_id
        state.erp_company_id = workspace.erp_company_id
        state.erp_company_name = str(connection.get("companyName") or "")[:200]
        state.erp_tenant_name = str(connection.get("tenantName") or "")[:200]
        state.enabled = True


def invite_url(workspace: Workspace, token: str) -> str:
    origin = workspace_origin(workspace.slug) if os.environ.get("CRM_BASE_DOMAIN") else ""
    return f"{origin}/?invite={token}"


def issue_invite(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        Invite(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            user_id=user.id,
            expires=datetime.now(UTC) + timedelta(hours=INVITE_HOURS),
        )
    )
    return token


def ensure_admin(workspace: Workspace, admin_password: str | None) -> str | None:
    body = workspace.request_body
    email = str(body["admin_email"]).lower()
    with workspace_db(workspace) as db:
        if db.scalar(select(User.id).where(User.email == email)):
            return None
        unusable = f"{secrets.token_hex(16)}:unusable"
        user = User(
            name=str(body.get("admin_name") or "Workspace Admin"),
            email=email,
            password_hash=password_hash(admin_password) if admin_password else unusable,
            role="admin",
        )
        db.add(user)
        db.flush()
        return None if admin_password else invite_url(workspace, issue_invite(db, user))


def provision_workspace(
    request: ProvisionRequest,
    actor: str,
    erp: ErpPlatform | None = None,
    admin_password: str | None = None,
    fail_at: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Run every provisioning step; each is idempotent, so re-running resumes after a failure."""
    if admin_password is not None and len(admin_password) < 12:
        raise ProvisionError(400, "VALIDATION", "Admin password must be at least 12 characters")
    workspace = record_request(request, actor)
    return run_steps(workspace.id, actor, erp or ErpPlatform(), admin_password, fail_at)


def run_steps(
    workspace_id: str,
    actor: str,
    erp: ErpPlatform,
    admin_password: str | None = None,
    fail_at: Callable[[str], None] | None = None,
) -> dict[str, object]:
    sessions = control_sessions()
    invite: str | None = None
    with workspace_lock(workspace_id), sessions() as control:
        workspace = control.get(Workspace, workspace_id, with_for_update=True)
        if workspace is None:
            raise ProvisionError(404, "NOT_FOUND", "Workspace not found")
        if workspace.status == "active":
            return public_state(workspace)
        if workspace.status == "suspended":
            raise ProvisionError(409, "WORKSPACE_SUSPENDED", "Suspended workspaces cannot be provisioned")
        workspace.status = "provisioning"
        workspace.last_error = None
        control.commit()
        target: Workspace = workspace

        def mark(step: str) -> None:
            if fail_at:
                fail_at(step)
            target.provision_step = step
            control.commit()

        try:
            mark("control_row")
            ensure_database(workspace)
            mark("database")
            run_migrations(workspace)
            mark("migrate")
            seed_settings(workspace)
            mark("settings")
            if not workspace.erp_binding_id:
                ids = bind_erp(workspace, erp)
                workspace.erp_binding_id = ids["bindingId"]
                workspace.erp_tenant_id = ids["tenantId"]
                workspace.erp_company_id = ids["companyId"]
            mark("erp_binding")
            active = control.scalar(
                select(WorkspaceCredential).where(
                    WorkspaceCredential.workspace_id == workspace.id, WorkspaceCredential.status == "active"
                )
            )
            if active is None:
                active = register_credential(control, workspace, erp)
                active.status = "active"
                active.activated_at = datetime.now(UTC)
            mark("credential")
            connection = verify_connection(workspace, decrypt(active.token_enc), erp)
            write_accounting_state(workspace, connection)
            mark("verify")
            invite = ensure_admin(workspace, admin_password)
            mark("admin")
            workspace.status = "active"
            provider_audit(control, workspace.id, actor, "workspace.activated", slug=workspace.slug)
            mark("active")
        except Exception as exc:
            control.rollback()
            workspace = control.get(Workspace, workspace_id)
            assert workspace is not None
            workspace.status = "failed"
            workspace.last_error = (
                f"{exc.code}: {exc.message}" if isinstance(exc, ProvisionError) else f"{type(exc).__name__}"
            )[:500]
            provider_audit(control, workspace.id, actor, "workspace.failed", step=workspace.provision_step)
            control.commit()
            raise
        result = public_state(workspace)
    if invite:
        result["inviteUrl"] = invite
    return result


def find_workspace(control: Session, reference: str) -> Workspace:
    workspace = control.scalar(
        select(Workspace).where((Workspace.slug == reference) | (Workspace.id == reference))
    )
    if workspace is None:
        raise ProvisionError(404, "NOT_FOUND", "Workspace not found")
    return workspace


def rotate_credential(reference: str, actor: str, erp: ErpPlatform | None = None) -> dict[str, object]:
    erp = erp or ErpPlatform()
    sessions = control_sessions()
    with sessions() as control:
        workspace_id = find_workspace(control, reference).id
    with workspace_lock(workspace_id), sessions() as control:
        workspace = control.get(Workspace, workspace_id)
        assert workspace is not None
        if workspace.status != "active":
            raise ProvisionError(409, "WORKSPACE_NOT_ACTIVE", "Only active workspaces can rotate credentials")
        pending = register_credential(control, workspace, erp)
        verify_connection(workspace, decrypt(pending.token_enc), erp)
        for old in control.scalars(
            select(WorkspaceCredential).where(
                WorkspaceCredential.workspace_id == workspace.id, WorkspaceCredential.status == "active"
            )
        ):
            old.status = "revoking"
        control.flush()
        pending.status = "active"
        pending.activated_at = datetime.now(UTC)
        provider_audit(control, workspace.id, actor, "credential.rotated", credentialId=pending.id)
        control.commit()
        revoked = []
        for old in control.scalars(
            select(WorkspaceCredential).where(
                WorkspaceCredential.workspace_id == workspace.id, WorkspaceCredential.status == "revoking"
            )
        ):
            response = erp.platform("DELETE", f"/crm-workspaces/{workspace.id}/credentials/{old.id}")
            if response.status_code not in {200, 204, 404}:
                raise erp_failure(response, "credential revocation")
            old.status = "revoked"
            old.revoked_at = datetime.now(UTC)
            revoked.append(old.id)
        control.commit()
        return {**public_state(workspace), "credentialId": pending.id, "revoked": revoked}


def suspend_workspace(reference: str, actor: str, erp: ErpPlatform | None = None) -> dict[str, object]:
    erp = erp or ErpPlatform()
    with control_sessions()() as control:
        workspace = find_workspace(control, reference)
        workspace.status = "suspended"
        provider_audit(control, workspace.id, actor, "workspace.suspended", slug=workspace.slug)
        control.commit()
        dispose_workspace(workspace.id)
        if workspace.erp_binding_id:
            response = erp.platform("POST", f"/crm-workspaces/{workspace.id}/suspend", {})
            if response.status_code not in {200, 201, 204}:
                raise erp_failure(response, "suspension")
        return public_state(workspace)


def reissue_invite(reference: str, email: str, actor: str) -> str:
    with control_sessions()() as control:
        workspace = find_workspace(control, reference)
        if workspace.status != "active":
            raise ProvisionError(409, "WORKSPACE_NOT_ACTIVE", "Workspace is not active")
        with workspace_db(workspace) as db:
            user = db.scalar(select(User).where(User.email == email.lower(), User.active.is_(True)))
            if user is None:
                raise ProvisionError(404, "NOT_FOUND", "User not found")
            token = issue_invite(db, user)
        provider_audit(control, workspace.id, actor, "invite.issued", email=email.lower())
        control.commit()
        return invite_url(workspace, token)
