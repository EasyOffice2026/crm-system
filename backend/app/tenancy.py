import os
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from .control import Workspace, control_sessions
from .secretbox import decrypt


def hosted() -> bool:
    return os.environ.get("CRM_TENANCY", "single") == "hosted"


@dataclass(frozen=True)
class WorkspaceContext:
    id: str
    slug: str
    origin: str
    upload_root: Path
    sessions: sessionmaker[Session]


current_workspace: ContextVar[WorkspaceContext | None] = ContextVar("current_workspace", default=None)
_engines: "OrderedDict[str, tuple[Engine, sessionmaker[Session]]]" = OrderedDict()
_engines_lock = Lock()


def base_domain() -> str:
    domain = os.environ.get("CRM_BASE_DOMAIN", "").strip().lower().strip(".")
    if not domain:
        raise RuntimeError("CRM_BASE_DOMAIN is required when CRM_TENANCY=hosted")
    return domain


def workspace_origin(slug: str) -> str:
    scheme = os.environ.get("CRM_WORKSPACE_SCHEME", "https")
    return f"{scheme}://{slug}.{base_domain()}"


def upload_base() -> Path:
    return Path(os.environ.get("CRM_UPLOAD_DIR", "./data/uploads"))


def workspace_database_url(workspace: Workspace) -> str:
    base = os.environ.get("CRM_WORKSPACE_DATABASE_URL", "")
    if not base:
        raise RuntimeError("CRM_WORKSPACE_DATABASE_URL is required when CRM_TENANCY=hosted")
    if base.startswith("sqlite"):
        directory = Path(make_url(base).database or ".")
        return f"sqlite:///{directory / (workspace.db_name + '.db')}"
    url = make_url(base).set(
        username=workspace.db_role, password=decrypt(workspace.db_password_enc), database=workspace.db_name
    )
    return url.render_as_string(hide_password=False)


def _sqlite_pragmas(connection: Any, record: object) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")


def make_workspace_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    return create_engine(url, pool_size=2, max_overflow=2, pool_pre_ping=True)


def workspace_sessions(workspace: Workspace) -> sessionmaker[Session]:
    limit = int(os.environ.get("CRM_MAX_OPEN_WORKSPACES", "50"))
    with _engines_lock:
        cached = _engines.get(workspace.id)
        if cached:
            _engines.move_to_end(workspace.id)
            return cached[1]
        engine = make_workspace_engine(workspace_database_url(workspace))
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        _engines[workspace.id] = (engine, factory)
        while len(_engines) > limit:
            _, (evicted, _factory) = _engines.popitem(last=False)
            evicted.dispose()
        return factory


def dispose_workspace(workspace_id: str) -> None:
    with _engines_lock:
        cached = _engines.pop(workspace_id, None)
    if cached:
        cached[0].dispose()


def dispose_all() -> None:
    with _engines_lock:
        values = list(_engines.values())
        _engines.clear()
    for engine, _factory in values:
        engine.dispose()


def context_for(workspace: Workspace) -> WorkspaceContext:
    return WorkspaceContext(
        id=workspace.id,
        slug=workspace.slug,
        origin=workspace_origin(workspace.slug),
        upload_root=Path(workspace.upload_root),
        sessions=workspace_sessions(workspace),
    )


def host_name(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host
    return host.rsplit(":", 1)[0] if ":" in host else host


def slug_for_host(host_header: str) -> str | None:
    host = host_name(host_header)
    suffix = "." + base_domain()
    if not host.endswith(suffix):
        return None
    slug = host[: -len(suffix)]
    return slug if slug and "." not in slug else None


def resolve_host(host_header: str) -> WorkspaceContext | None:
    slug = slug_for_host(host_header)
    if not slug:
        return None
    with control_sessions()() as control:
        workspace = control.scalar(select(Workspace).where(Workspace.slug == slug))
        if not workspace or workspace.status != "active":
            return None
        return context_for(workspace)


def provider_host() -> str:
    return os.environ.get("CRM_PROVIDER_HOST", "").strip().lower()
