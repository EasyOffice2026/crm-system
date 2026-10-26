import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import db_session
from .models import LoginSession, Settings, User
from .tenancy import hosted

Db = Annotated[Session, Depends(db_session, scope="function")]
PRODUCTION = os.environ.get("CRM_ENV") == "production"


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f"{salt}:{digest}"


def verify_password(password: str, stored: str) -> bool:
    salt, expected = stored.split(":")
    actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return hmac.compare_digest(actual, expected)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def request_workspace_id(request: Request) -> str | None:
    workspace = getattr(request.state, "workspace", None)
    return workspace.id if workspace is not None else None


def new_session_token(request: Request) -> str:
    workspace_id = request_workspace_id(request)
    token = secrets.token_urlsafe(40)
    return f"{workspace_id}.{token}" if workspace_id else token


def cookie_secure() -> bool:
    return PRODUCTION or hosted()


def current_user(request: Request, db: Db, crm_session: Annotated[str | None, Cookie()] = None) -> User:
    if not crm_session:
        raise HTTPException(401, "Please sign in")
    if hosted():
        workspace_id = request_workspace_id(request)
        prefix, separator, _ = crm_session.partition(".")
        if not workspace_id or not separator or not hmac.compare_digest(prefix, workspace_id):
            raise HTTPException(401, "Please sign in")
    session = db.get(LoginSession, token_hash(crm_session))
    if not session or session.expires.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        raise HTTPException(401, "Your session has expired")
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise HTTPException(401, "Account is inactive")
    return user


Actor = Annotated[User, Depends(current_user)]


def require(user: User, *roles: str) -> None:
    if user.role not in roles:
        raise HTTPException(403, "Your role cannot perform this action")


def write_guard(request: Request, db: Db) -> None:
    origin = request.headers.get("origin")
    if hosted():
        workspace = getattr(request.state, "workspace", None)
        if workspace is None:
            raise HTTPException(404, "Workspace not found")
        public_origin = workspace.origin
    else:
        public_origin = os.environ.get("CRM_PUBLIC_ORIGIN") or str(request.base_url)
    if origin and origin.rstrip("/") != public_origin.rstrip("/"):
        raise HTTPException(403, "Cross-origin writes are not allowed")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site writes are not allowed")
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    else:
        db.execute(select(Settings).where(Settings.id == 1).with_for_update())
