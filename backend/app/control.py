import os
import re
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
RESERVED_SLUGS = {"www", "api", "admin", "provider", "app"}


def now() -> datetime:
    return datetime.now(timezone.utc)


class ControlBase(DeclarativeBase):
    pass


class Workspace(ControlBase):
    __tablename__ = "crm_workspaces"
    __table_args__ = (
        CheckConstraint(
            "status IN ('provisioning','active','suspended','failed')", name="ck_workspace_status"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(41), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="provisioning")
    provision_request_id: Mapped[str] = mapped_column(String(36), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    request_body: Mapped[dict[str, object]] = mapped_column(JSON)
    provision_step: Mapped[str] = mapped_column(String(40), default="control_row")
    last_error: Mapped[str | None] = mapped_column(Text)
    db_name: Mapped[str] = mapped_column(String(63), unique=True)
    db_role: Mapped[str] = mapped_column(String(63))
    db_password_enc: Mapped[str] = mapped_column(Text)
    upload_root: Mapped[str] = mapped_column(Text)
    erp_tenant_id: Mapped[str | None] = mapped_column(String(36))
    erp_company_id: Mapped[str | None] = mapped_column(String(36))
    erp_binding_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class WorkspaceCredential(ControlBase):
    __tablename__ = "crm_workspace_credentials"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','active','revoking','revoked')", name="ck_workspace_credential_status"
        ),
        Index(
            "uq_workspace_active_credential",
            "workspace_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("crm_workspaces.id"))
    token_enc: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderAudit(ControlBase):
    __tablename__ = "crm_provider_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(String(36))
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(120))
    detail: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


_control: tuple[str, Engine, sessionmaker[Session]] | None = None


def control_url() -> str:
    url = os.environ.get("CRM_CONTROL_DATABASE_URL", "")
    if not url:
        raise RuntimeError("CRM_CONTROL_DATABASE_URL is required when CRM_TENANCY=hosted")
    return url


def control_sessions() -> sessionmaker[Session]:
    global _control
    url = control_url()
    if _control is None or _control[0] != url:
        if _control is not None:
            _control[1].dispose()
        engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {},
        )
        _control = (url, engine, sessionmaker(bind=engine, expire_on_commit=False))
    return _control[2]


def control_engine() -> Engine:
    control_sessions()
    assert _control is not None
    return _control[1]


def reset_control() -> None:
    global _control
    if _control is not None:
        _control[1].dispose()
    _control = None


def provider_audit(db: Session, workspace_id: str | None, actor: str, action: str, **detail: object) -> None:
    db.add(ProviderAudit(workspace_id=workspace_id, actor=actor, action=action, detail=detail))
