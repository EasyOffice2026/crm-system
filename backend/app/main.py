import csv
import hashlib
import io
import json
import os
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from .accounting import ACCOUNT_TYPES, journal_payload, pending_entries
from .control import control_url
from .db import Base, SessionLocal, engine
from .erp import erp_get, erp_post
from .migrate import prepare_single
from .models import (
    AccountingOutbox,
    AccountingState,
    Allocation,
    Approval,
    Attachment,
    Audit,
    Customer,
    Document,
    DocumentRevision,
    ErpLink,
    ErpPosting,
    Invite,
    LedgerEntry,
    LoginSession,
    Opportunity,
    Payment,
    Product,
    Settings,
    Task,
    User,
)
from .outbox import accounting_for, enqueue_for_ledger
from .pdf import make_pdf
from .provider_api import router as provider_router
from .schemas import (
    ActionInput,
    AllocationInput,
    CustomerInput,
    DecisionInput,
    DocumentInput,
    ErpActivationInput,
    InviteAcceptInput,
    ItemInput,
    LoginInput,
    OpportunityInput,
    PaymentInput,
    ProductInput,
    SettingsInput,
    TaskInput,
    UserInput,
)
from .security import (
    PRODUCTION,
    Actor,
    Db,
    cookie_secure,
    new_session_token,
    password_hash,
    request_workspace_id,
    require,
    token_hash,
    verify_password,
    write_guard,
)
from .services import (
    HOSTED_MANUAL_KINDS,
    audit,
    customer,
    customer_balance,
    document,
    document_data,
    ensure_postable_kind,
    invoice_balance,
    make_document,
    minor,
    money,
    post_document,
    settings,
    unallocated_credit,
)
from .tenancy import (
    base_domain,
    current_workspace,
    dispose_all,
    host_name,
    hosted,
    provider_host,
    resolve_host,
)

UPLOADS = Path(os.environ.get("CRM_UPLOAD_DIR", "./data/uploads"))
failed_logins: dict[str, list[float]] = {}
LOGIN_THROTTLE_MAX_KEYS = 10000
LOGIN_ADDRESS_LIMIT = 50


def record_login_attempt(key: str, attempts: list[float], now: float) -> None:
    # When full, drop expired keys, then the least active ones; locked-out keys go last, so a flood
    # of new keys cannot reset the lockout of an account that is under attack.
    if key not in failed_logins and len(failed_logins) >= LOGIN_THROTTLE_MAX_KEYS:
        for stale in [name for name, times in failed_logins.items() if not times or times[-1] <= now - 300]:
            del failed_logins[stale]
        excess = len(failed_logins) - LOGIN_THROTTLE_MAX_KEYS + 1
        if excess > 0:
            ranked = sorted(failed_logins.items(), key=lambda item: (len(item[1]) >= 10, item[1][-1]))
            for name, _ in ranked[:excess]:
                del failed_logins[name]
    failed_logins[key] = attempts


login_lock = Lock()
DUMMY_HASH = password_hash(secrets.token_hex(32))


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    if hosted():
        base_domain()
        control_url()
        yield
        dispose_all()
        return
    prepare_single(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if not db.get(Settings, 1):
            db.add(Settings(id=1, is_demo=not PRODUCTION and not os.environ.get("CRM_ADMIN_PASSWORD")))
        config = settings(db)
        if PRODUCTION and config.is_demo:
            raise RuntimeError("Use a fresh production database; demo workspaces cannot run in production")
        if not db.scalar(select(User.id).limit(1)):
            password = os.environ.get("CRM_ADMIN_PASSWORD", "")
            if PRODUCTION and len(password) < 12:
                raise RuntimeError(
                    "Set CRM_ADMIN_PASSWORD to at least 12 characters before production startup"
                )
            db.add(
                User(
                    name="Workspace Admin",
                    email=os.environ.get("CRM_ADMIN_EMAIL", "admin@crm.local").lower(),
                    password_hash=password_hash(password or "CrmDemo!2026"),
                    role="admin",
                )
            )
        db.commit()
    yield


app = FastAPI(title="Nexera CRM", version="0.1.0", lifespan=lifespan)
api = APIRouter(prefix="/api")
mutations = APIRouter(prefix="/api", dependencies=[Depends(write_guard)])


@app.middleware("http")
async def resolve_workspace(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if not hosted():
        return await call_next(request)
    host = request.headers.get("host", "")
    path = request.url.path
    is_provider = bool(provider_host()) and host_name(host) == provider_host()
    if is_provider or path.startswith("/provider/"):
        if not is_provider or not path.startswith("/provider/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return await call_next(request)
    if path == "/api/health":
        return await call_next(request)
    workspace = await run_in_threadpool(resolve_host, host)
    if workspace is None:
        return JSONResponse({"detail": "Workspace not found"}, status_code=404)
    request.state.workspace = workspace
    reset = current_workspace.set(workspace)
    try:
        return await call_next(request)
    finally:
        current_workspace.reset(reset)


@app.middleware("http")
async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith(("/api", "/provider")):
        response.headers["Cache-Control"] = "no-store"
    return response


def user_data(user: User) -> dict[str, object]:
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "active": user.active}


def settings_data(config: Settings) -> dict[str, object]:
    return {
        "company": config.company,
        "address": config.address,
        "tax_id": config.tax_id,
        "currency": config.currency,
        "decimals": config.decimals,
        "tax_rate": config.tax_rate,
        "payment_days": config.payment_days,
        "terms": config.terms,
        "approval_threshold": money(config.approval_threshold, config.decimals),
        "discount_threshold": config.discount_threshold,
        "number_prefixes": config.number_prefixes,
    }


def check_owner(user: User, owner_id: int) -> None:
    if user.role == "sales" and owner_id != user.id:
        raise HTTPException(403, "Salespeople can change only their assigned records")


def check_assignment(db: Db, user: User, owner_id: int, customer_id: int | None) -> None:
    check_owner(user, owner_id)
    owner = db.get(User, owner_id)
    if not owner or not owner.active or owner.role == "finance":
        raise HTTPException(400, "Select an active sales, manager or admin owner")
    if customer_id:
        customer(db, customer_id)


@api.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@mutations.post("/auth/login")
def login(body: LoginInput, request: Request, response: Response, db: Db) -> dict[str, object]:
    address = request.client.host if request.client else "unknown"
    workspace_id = request_workspace_id(request)
    key = f"{workspace_id}:{body.email.lower()}:{address}" if workspace_id else address
    address_key = f"{workspace_id}::{address}" if workspace_id else None
    now = time.monotonic()
    with login_lock:
        attempts = [value for value in failed_logins.get(key, []) if value > now - 300]
        address_attempts = (
            [value for value in failed_logins.get(address_key, []) if value > now - 300]
            if address_key
            else []
        )
        if len(attempts) >= 10 or len(address_attempts) >= LOGIN_ADDRESS_LIMIT:
            raise HTTPException(429, "Too many attempts. Try again in five minutes.")
        record_login_attempt(key, attempts + [now], now)
        if address_key:
            record_login_attempt(address_key, address_attempts + [now], now)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    valid = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if not user or not valid or not user.active:
        raise HTTPException(401, "Incorrect email or password")
    with login_lock:
        failed_logins.pop(key, None)
        if address_key and now in failed_logins.get(address_key, []):
            failed_logins[address_key].remove(now)
    token = new_session_token(request)
    db.execute(delete(LoginSession).where(LoginSession.expires < datetime.now(timezone.utc)))
    db.add(
        LoginSession(
            token_hash=token_hash(token),
            user_id=user.id,
            expires=datetime.now(timezone.utc) + timedelta(hours=12),
        )
    )
    response.set_cookie(
        "crm_session",
        token,
        httponly=True,
        secure=cookie_secure(),
        samesite="strict",
        max_age=43200,
        path="/",
    )
    audit(db, user, "Signed in")
    return user_data(user)


@api.get("/auth/me")
def me(user: Actor) -> dict[str, object]:
    return user_data(user)


@mutations.post("/auth/logout")
def logout(request: Request, response: Response, db: Db, user: Actor) -> dict[str, bool]:
    db.execute(
        delete(LoginSession).where(
            LoginSession.token_hash == token_hash(request.cookies.get("crm_session", ""))
        )
    )
    response.delete_cookie("crm_session", path="/", secure=cookie_secure(), httponly=True, samesite="strict")
    return {"ok": True}


@api.get("/settings")
def get_settings(db: Db, user: Actor) -> dict[str, object]:
    return settings_data(settings(db))


@api.get("/workspace")
def workspace_mode(user: Actor) -> dict[str, object]:
    return {"hosted": hosted(), "manual_ledger_kinds": sorted(HOSTED_MANUAL_KINDS) if hosted() else []}


@api.get("/accounting/connection")
def accounting_connection(db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager", "finance")
    result = erp_get("connection")
    if not isinstance(result, dict):
        raise HTTPException(502, "ERP returned an invalid connection")
    link: ErpLink | AccountingState | None
    if hosted():
        link = db.get(AccountingState, 1)
        expected = (link.erp_tenant_id, link.erp_company_id) if link else None
    else:
        link = db.get(ErpLink, 1)
        expected = (link.tenant_id, link.company_id) if link else None
    if expected and (result.get("tenantId"), result.get("companyId")) != expected:
        raise HTTPException(409, "ERP credentials point to a different organization or company")
    config = settings(db)
    if (result.get("currency"), config.currency, config.decimals) != ("KWD", "KWD", 3):
        raise HTTPException(409, "CRM and ERP company currencies do not match")
    return result


@api.get("/accounting/accounts")
def accounting_accounts(db: Db, user: Actor) -> object:
    accounting_connection(db, user)
    return erp_get("accounts")


@api.get("/accounting/trial-balance")
def accounting_trial_balance(db: Db, user: Actor, start: date, end: date) -> object:
    if start > end:
        raise HTTPException(400, "Start date must precede end date")
    accounting_connection(db, user)
    return erp_get("trial-balance", {"from": str(start), "to": str(end)})


@api.get("/accounting/journals")
def accounting_journals(db: Db, user: Actor) -> object:
    accounting_connection(db, user)
    return erp_get("journals")


@api.get("/accounting/sync-status")
def accounting_sync_status(db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager", "finance")
    link = db.get(ErpLink, 1)
    cutoff = link.cutoff_ledger_id if link else db.scalar(select(func.max(LedgerEntry.id))) or 0
    pending = (
        db.scalar(
            select(func.count(LedgerEntry.id))
            .outerjoin(ErpPosting, ErpPosting.ledger_id == LedgerEntry.id)
            .where(LedgerEntry.id > cutoff, ErpPosting.ledger_id.is_(None))
        )
        or 0
    )
    return {
        "active": link is not None,
        "enabled": os.environ.get("CRM_ERP_SYNC_ENABLED") == "true",
        "cutoff_ledger_id": cutoff,
        "historical_count": db.scalar(select(func.count(LedgerEntry.id)).where(LedgerEntry.id <= cutoff))
        or 0,
        "customer_balance": money(
            db.scalar(select(func.coalesce(func.sum(LedgerEntry.debit - LedgerEntry.credit), 0))) or 0,
            settings(db).decimals,
        ),
        "pending": pending,
        "postings": [
            {
                "ledger_id": posting.ledger_id,
                "journal_id": posting.journal_id,
                "number": posting.journal_number,
            }
            for posting in db.scalars(select(ErpPosting).order_by(ErpPosting.ledger_id.desc()).limit(50))
        ],
    }


def legacy_activation_only() -> None:
    if hosted():
        raise HTTPException(410, "Hosted workspaces are linked to the ERP by the provider")


@mutations.post("/accounting/activate", dependencies=[Depends(legacy_activation_only)])
def activate_accounting(body: ErpActivationInput, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin")
    connection = accounting_connection(db, user)
    if (connection.get("tenantId"), connection.get("companyId")) != (body.tenant_id, body.company_id):
        raise HTTPException(409, "Confirm the organization and company shown by the connection")
    if db.get(ErpLink, 1):
        raise HTTPException(409, "This workspace already has a fixed ERP company mapping")
    cutoff = db.scalar(select(func.max(LedgerEntry.id))) or 0
    if body.cutoff_ledger_id != cutoff:
        raise HTTPException(409, "New transactions were recorded; review reconciliation again")
    available = erp_get("accounts")
    if not isinstance(available, list):
        raise HTTPException(502, "ERP returned invalid accounts")
    accounts = {row["id"]: row for row in available if isinstance(row, dict) and "id" in row}
    account_ids = {str(key): value for key, value in body.account_ids.items()}
    for key, types in ACCOUNT_TYPES.items():
        identifier = account_ids.get(key)
        if key == "tax" and not identifier:
            continue
        account = accounts.get(identifier)
        if (
            not account
            or account.get("type") not in types
            or not account.get("isPostable")
            or not account.get("isActive")
        ):
            raise HTTPException(400, f"Choose an active postable {key} account of the correct type")
    if body.account_ids["receivable"] == body.account_ids["receipts"]:
        raise HTTPException(400, "Receivables and receipt clearing must use different accounts")
    db.add(
        ErpLink(
            id=1,
            workspace_id=str(uuid4()),
            tenant_id=body.tenant_id,
            company_id=body.company_id,
            cutoff_ledger_id=cutoff,
            account_ids=body.account_ids,
        )
    )
    audit(db, user, "ERP company linked", detail=f"{body.company_id}; historical ledger cutoff {cutoff}")
    db.flush()
    return accounting_sync_status(db, user)


@mutations.post("/accounting/sync")
def sync_accounting(db: Db, user: Actor) -> dict[str, object]:
    if hosted():
        require(user, "admin", "finance")
        rows = list(
            db.scalars(select(AccountingOutbox).where(AccountingOutbox.status.in_(("retrying", "blocked"))))
        )
        for row in rows:
            row.status = "pending"
            row.next_attempt_at = datetime.now(timezone.utc)
        if rows:
            audit(db, user, "Accounting retry requested", detail=f"{len(rows)} event(s) scheduled")
        db.flush()
        return accounting_status(db, user)
    require(user, "admin", "manager", "finance")
    if os.environ.get("CRM_ERP_SYNC_ENABLED") != "true":
        raise HTTPException(409, "ERP synchronization is disabled until reconciliation is approved")
    accounting_connection(db, user)
    link = db.get(ErpLink, 1)
    if not link:
        raise HTTPException(409, "Review reconciliation and map the ERP company first")
    posted = 0
    failure = None
    for entry in pending_entries(db, link):
        try:
            payload = journal_payload(db, link, entry)
            result = erp_post("journals", payload)
            if not isinstance(result, dict):
                raise HTTPException(502, "ERP returned an invalid journal link")
            try:
                journal_id = str(UUID(str(result["id"])))
                number = result["number"]
                if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
                    raise ValueError
            except (KeyError, ValueError) as exc:
                raise HTTPException(502, "ERP returned an invalid journal link") from exc
            db.add(
                ErpPosting(ledger_id=entry.id, journal_id=journal_id, journal_number=number, payload=payload)
            )
            audit(db, user, "ERP journal linked", entry.customer_id, f"Ledger {entry.id} → Journal {number}")
            db.flush()
            posted += 1
        except HTTPException as exc:
            failure = f"Ledger {entry.id}: {exc.detail}"
            break
    return {"posted": posted, "failure": failure, **accounting_sync_status(db, user)}


OUTBOX_COUNT_KEYS = {
    "pending": "pending",
    "in_progress": "inProgress",
    "retrying": "retrying",
    "blocked": "blocked",
    "held": "held",
    "conflict": "conflict",
    "posted": "posted",
    "skipped": "skipped",
}


@api.get("/accounting/status")
def accounting_status(db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager", "finance")
    state = db.get(AccountingState, 1)
    counts = {value: 0 for value in OUTBOX_COUNT_KEYS.values()}
    for status, count in db.execute(
        select(AccountingOutbox.status, func.count(AccountingOutbox.id)).group_by(AccountingOutbox.status)
    ):
        counts[OUTBOX_COUNT_KEYS[status]] = count
    last_posted = db.scalar(select(func.max(AccountingOutbox.posted_at)))
    config = settings(db)
    return {
        "mode": "hosted" if hosted() else "single",
        "enabled": bool(state and state.enabled),
        "pausedReason": state.paused_reason if state else None,
        "erp": {
            "tenantId": state.erp_tenant_id if state else None,
            "companyId": state.erp_company_id if state else None,
            "companyName": state.erp_company_name if state else "",
            "currency": config.currency,
        },
        "counts": counts,
        "lastPostedAt": last_posted.isoformat() if last_posted else None,
    }


def outbox_data(row: AccountingOutbox) -> dict[str, object]:
    payload = row.payload
    amounts = payload.get("amounts")
    return {
        "id": row.id,
        "eventId": row.event_id,
        "type": row.event_type,
        "sourceType": row.source_type,
        "sourceId": row.source_id,
        "reference": payload.get("reference"),
        "date": payload.get("date"),
        "total": amounts.get("total") if isinstance(amounts, dict) else None,
        "status": row.status,
        "attempts": row.attempts,
        "nextAttemptAt": row.next_attempt_at.isoformat() if row.next_attempt_at else None,
        "lastErrorCode": row.last_error_code,
        "lastErrorMessage": row.last_error_message,
        "erpJournalNumber": row.erp_journal_number,
        "postedAt": row.posted_at.isoformat() if row.posted_at else None,
    }


@api.get("/accounting/outbox")
def accounting_outbox(
    db: Db,
    user: Actor,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: int | None = None,
) -> list[dict[str, object]]:
    require(user, "admin", "manager", "finance")
    query = select(AccountingOutbox).order_by(AccountingOutbox.id.desc()).limit(limit)
    if status:
        if status not in OUTBOX_COUNT_KEYS:
            raise HTTPException(400, "Unknown posting status")
        query = query.where(AccountingOutbox.status == status)
    if before_id:
        query = query.where(AccountingOutbox.id < before_id)
    return [outbox_data(row) for row in db.scalars(query)]


@mutations.post("/accounting/outbox/{identifier}/retry")
def retry_outbox(identifier: int, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "finance")
    row = db.get(AccountingOutbox, identifier)
    if not row:
        raise HTTPException(404, "Posting event not found")
    if row.status not in {"blocked", "retrying", "held"}:
        raise HTTPException(409, "Only blocked, retrying or held events can be retried")
    row.status = "pending"
    row.next_attempt_at = datetime.now(timezone.utc)
    state = db.get(AccountingState, 1)
    if state and state.paused_reason:
        state.paused_at = None
    ledger = db.get(LedgerEntry, row.ledger_id)
    audit(db, user, "Accounting retry requested", ledger.customer_id if ledger else None, row.event_id)
    db.flush()
    return outbox_data(row)


@mutations.post("/auth/accept-invite")
def accept_invite(body: InviteAcceptInput, db: Db) -> dict[str, bool]:
    invite = db.get(Invite, token_hash(body.token))
    expires = invite.expires if invite else None
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if not invite or invite.used_at or not expires or expires < datetime.now(timezone.utc):
        raise HTTPException(400, "This invitation link is invalid or has expired")
    member = db.get(User, invite.user_id)
    if not member or not member.active:
        raise HTTPException(400, "This invitation link is invalid or has expired")
    member.password_hash = password_hash(body.password)
    invite.used_at = datetime.now(timezone.utc)
    db.execute(delete(LoginSession).where(LoginSession.user_id == member.id))
    db.add(Audit(actor_id=member.id, action="Invitation accepted", detail=member.email))
    return {"ok": True}


@mutations.put("/settings")
def save_settings(body: SettingsInput, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin")
    config = settings(db)
    expected = 3 if body.currency in {"KWD", "BHD", "OMR"} else 2
    if body.decimals != expected:
        raise HTTPException(400, f"{body.currency} requires {expected} decimal places")
    if (config.currency, config.decimals) != (body.currency, body.decimals):
        if (
            db.scalar(select(Document.id).limit(1))
            or db.scalar(select(Product.id).limit(1))
            or db.scalar(select(Opportunity.id).limit(1))
            or db.scalar(select(Payment.id).limit(1))
        ):
            raise HTTPException(409, "Currency cannot change after priced records exist")
    config.company, config.address, config.tax_id = body.company, body.address, body.tax_id
    config.currency, config.decimals, config.payment_days = body.currency, body.decimals, body.payment_days
    config.terms = body.terms
    config.tax_rate, config.discount_threshold = str(body.tax_rate), str(body.discount_threshold)
    config.approval_threshold = minor(body.approval_threshold, config.decimals)
    config.number_prefixes = body.number_prefixes
    audit(db, user, "Workspace settings updated")
    return settings_data(config)


@api.get("/users")
def users(db: Db, user: Actor) -> list[dict[str, object]]:
    return [user_data(value) for value in db.scalars(select(User).order_by(User.name))]


@mutations.post("/users", status_code=201)
def create_user(body: UserInput, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin")
    if db.scalar(select(User.id).where(User.email == body.email.lower())):
        raise HTTPException(409, "An account with this email already exists")
    result = User(
        name=body.name, email=body.email.lower(), password_hash=password_hash(body.password), role=body.role
    )
    db.add(result)
    db.flush()
    audit(db, user, "User created", detail=f"{result.email} ({result.role})")
    return user_data(result)


@mutations.post("/users/{identifier}/toggle")
def toggle_user(identifier: int, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin")
    result = db.get(User, identifier)
    if not result:
        raise HTTPException(404, "User not found")
    if result.id == user.id:
        raise HTTPException(400, "You cannot deactivate your own account")
    result.active = not result.active
    if not result.active:
        db.execute(delete(LoginSession).where(LoginSession.user_id == result.id))
    audit(db, user, "User access changed", detail=result.email)
    return user_data(result)


@api.get("/customers")
def customers(db: Db, user: Actor) -> list[dict[str, object]]:
    precision = settings(db).decimals
    return [
        dict(
            jsonable_encoder(value),
            balance=money(customer_balance(db, value.id), precision),
            unallocated=money(unallocated_credit(db, value.id), precision),
        )
        for value in db.scalars(select(Customer).order_by(Customer.name))
    ]


@mutations.post("/customers", status_code=201)
def create_customer(body: CustomerInput, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager", "sales")
    if body.email and db.scalar(select(Customer.id).where(func.lower(Customer.email) == body.email.lower())):
        raise HTTPException(409, "A customer already uses this email. Review the existing record first.")
    result = Customer(**body.model_dump())
    db.add(result)
    db.flush()
    audit(db, user, "Customer created", result.id, result.name)
    return jsonable_encoder(result)


@mutations.put("/customers/{identifier}")
def update_customer(identifier: int, body: CustomerInput, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager", "sales")
    result = customer(db, identifier)
    if body.email and db.scalar(
        select(Customer.id).where(func.lower(Customer.email) == body.email.lower(), Customer.id != identifier)
    ):
        raise HTTPException(409, "A customer already uses this email")
    result.name, result.kind, result.email, result.phone = body.name, body.kind, body.email, body.phone
    result.contact, result.address, result.tax_id = body.contact, body.address, body.tax_id
    result.tags, result.payment_days, result.notes = body.tags, body.payment_days, body.notes
    result.contacts = [value.model_dump() for value in body.contacts]
    result.addresses = [value.model_dump() for value in body.addresses]
    audit(db, user, "Customer updated", result.id, result.name)
    return jsonable_encoder(result)


@api.get("/customers/{identifier}/history")
def customer_history(identifier: int, db: Db, user: Actor) -> dict[str, object]:
    customer(db, identifier)
    return {
        "activities": jsonable_encoder(
            list(db.scalars(select(Audit).where(Audit.customer_id == identifier).order_by(Audit.id.desc())))
        ),
        "attachments": [
            {"id": value.id, "name": value.name, "size": value.size}
            for value in db.scalars(select(Attachment).where(Attachment.customer_id == identifier))
        ],
    }


@mutations.post("/customers/{identifier}/attachments", status_code=201)
def upload_attachment(
    identifier: int, file: UploadFile, request: Request, db: Db, user: Actor
) -> dict[str, object]:
    require(user, "admin", "manager", "sales")
    customer(db, identifier)
    content = file.file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "Maximum attachment size is 10 MB")
    name = Path(file.filename or "attachment").name[:250]
    if Path(name).suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".csv", ".txt", ".docx", ".xlsx"}:
        raise HTTPException(400, "Use PDF, image, CSV, text, Word or Excel files")
    root = upload_root(request)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = secrets.token_hex(24)
    upload_path(root, key).write_bytes(content)
    result = Attachment(customer_id=identifier, name=name, storage_key=key, size=len(content))
    db.add(result)
    db.flush()
    audit(db, user, "Attachment uploaded", identifier, name)
    return {"id": result.id, "name": name, "size": result.size}


@api.get("/attachments/{identifier}")
def download_attachment(identifier: int, request: Request, db: Db, user: Actor) -> FileResponse:
    result = db.get(Attachment, identifier)
    path = upload_path(upload_root(request), result.storage_key) if result else None
    if not result or not path or not path.is_file():
        raise HTTPException(404, "Attachment not found")
    return FileResponse(path, media_type="application/octet-stream", filename=result.name)


def upload_root(request: Request) -> Path:
    workspace = getattr(request.state, "workspace", None)
    return workspace.upload_root if workspace is not None else UPLOADS


def upload_path(root: Path, key: str) -> Path:
    base = root.resolve()
    path = (base / key).resolve()
    if path.parent != base:
        raise HTTPException(404, "Attachment not found")
    return path


def csv_response(rows: list[list[str]], filename: str) -> Response:
    output = io.StringIO()
    writer = csv.writer(output)
    for row in rows:
        writer.writerow(
            ["'" + cell if cell.startswith(("=", "+", "-", "@", "\t", "\r")) else cell for cell in row]
        )
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api.get("/exports/customers")
def export_customers(db: Db, user: Actor) -> Response:
    rows = [["name", "email", "phone", "contact", "address", "tax_id", "tags", "payment_days", "notes"]]
    rows.extend(
        [
            [
                value.name,
                value.email,
                value.phone,
                value.contact,
                value.address,
                value.tax_id,
                value.tags,
                str(value.payment_days),
                value.notes,
            ]
            for value in db.scalars(select(Customer).order_by(Customer.id))
        ]
    )
    return csv_response(rows, "customers.csv")


@mutations.post("/imports/customers")
def import_customers(file: UploadFile, db: Db, user: Actor) -> dict[str, int]:
    require(user, "admin", "manager", "sales")
    raw = file.file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, "Maximum CSV size is 2 MB")
    try:
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        if not rows or len(rows) > 1000:
            raise ValueError("CSV must contain 1 to 1000 customer rows")
        for row in rows:
            body = CustomerInput.model_validate(row)
            create_customer(body, db, user)
    except (UnicodeDecodeError, ValueError, ValidationError) as error:
        raise HTTPException(400, f"Invalid customer CSV: {error}") from error
    return {"imported": len(rows)}


@api.get("/products")
def products(db: Db, user: Actor) -> list[dict[str, object]]:
    precision = settings(db).decimals
    return [
        dict(jsonable_encoder(value), price=money(value.price, precision))
        for value in db.scalars(select(Product).order_by(Product.name))
    ]


@mutations.post("/products", status_code=201)
def create_product(body: ProductInput, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager")
    result = Product(
        name=body.name,
        description=body.description,
        unit=body.unit,
        price=minor(body.price, settings(db).decimals),
        active=body.active,
    )
    db.add(result)
    db.flush()
    audit(db, user, "Catalogue item created", detail=body.name)
    return dict(jsonable_encoder(result), price=money(result.price, settings(db).decimals))


@mutations.put("/products/{identifier}")
def update_product(identifier: int, body: ProductInput, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager")
    result = db.get(Product, identifier)
    if not result:
        raise HTTPException(404, "Catalogue item not found")
    result.name, result.description, result.unit = body.name, body.description, body.unit
    result.price, result.active = minor(body.price, settings(db).decimals), body.active
    audit(db, user, "Catalogue item updated", detail=body.name)
    return dict(jsonable_encoder(result), price=money(result.price, settings(db).decimals))


@api.get("/opportunities")
def opportunities(db: Db, user: Actor) -> list[dict[str, object]]:
    query = select(Opportunity).order_by(Opportunity.id.desc())
    if user.role == "sales":
        query = query.where(Opportunity.owner_id == user.id)
    return [
        dict(jsonable_encoder(value), value=money(value.value, settings(db).decimals))
        for value in db.scalars(query)
    ]


@mutations.post("/opportunities", status_code=201)
def create_opportunity(body: OpportunityInput, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager", "sales")
    check_assignment(db, user, body.owner_id, body.customer_id)
    result = Opportunity(
        title=body.title,
        customer_id=body.customer_id,
        owner_id=body.owner_id,
        source=body.source,
        stage=body.stage,
        value=minor(body.value, settings(db).decimals),
        closing_date=str(body.closing_date or ""),
        notes=body.notes,
    )
    db.add(result)
    db.flush()
    audit(db, user, "Opportunity created", body.customer_id, body.title)
    return dict(jsonable_encoder(result), value=money(result.value, settings(db).decimals))


@mutations.put("/opportunities/{identifier}")
def update_opportunity(identifier: int, body: OpportunityInput, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager", "sales")
    result = db.get(Opportunity, identifier)
    if not result:
        raise HTTPException(404, "Opportunity not found")
    check_owner(user, result.owner_id)
    check_assignment(db, user, body.owner_id, body.customer_id)
    result.title, result.customer_id, result.owner_id = body.title, body.customer_id, body.owner_id
    result.source, result.stage, result.notes = body.source, body.stage, body.notes
    result.value, result.closing_date = minor(body.value, settings(db).decimals), str(body.closing_date or "")
    audit(db, user, "Opportunity updated", body.customer_id, f"{body.title}: {body.stage}")
    return dict(jsonable_encoder(result), value=money(result.value, settings(db).decimals))


@api.get("/tasks")
def tasks(db: Db, user: Actor) -> list[dict[str, object]]:
    query = select(Task).order_by(Task.done, Task.due)
    if user.role == "sales":
        query = query.where(Task.owner_id == user.id)
    return jsonable_encoder(list(db.scalars(query)))


@mutations.post("/tasks", status_code=201)
def create_task(body: TaskInput, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager", "sales")
    check_assignment(db, user, body.owner_id, body.customer_id)
    result = Task(
        title=body.title,
        customer_id=body.customer_id,
        owner_id=body.owner_id,
        due=str(body.due),
        notes=body.notes,
    )
    db.add(result)
    db.flush()
    audit(db, user, "Follow-up scheduled", body.customer_id, body.title)
    return jsonable_encoder(result)


@mutations.post("/tasks/{identifier}/toggle")
def toggle_task(identifier: int, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager", "sales")
    result = db.get(Task, identifier)
    if not result:
        raise HTTPException(404, "Task not found")
    check_owner(user, result.owner_id)
    result.done = not result.done
    audit(
        db,
        user,
        "Follow-up completed" if result.done else "Follow-up reopened",
        result.customer_id,
        result.title,
    )
    return jsonable_encoder(result)


@api.get("/documents")
def documents(db: Db, user: Actor) -> list[dict[str, object]]:
    query = select(Document).order_by(Document.id.desc())
    if user.role == "sales":
        query = query.where(Document.author_id == user.id)
    return [document_data(db, value) for value in db.scalars(query)]


@mutations.post("/documents", status_code=201)
def create_document(body: DocumentInput, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager", "sales" if body.kind == "quote" else "finance")
    ensure_postable_kind(body.kind)
    return document_data(db, make_document(db, body, user))


@api.get("/documents/{identifier}/revisions")
def document_revisions(identifier: int, db: Db, user: Actor) -> list[dict[str, object]]:
    source = document(db, identifier)
    check_owner(user, source.author_id)
    archived = [
        value.content
        for value in db.scalars(
            select(DocumentRevision)
            .where(DocumentRevision.document_id == identifier)
            .order_by(DocumentRevision.revision)
        )
    ]
    return [*archived, document_data(db, source)]


@mutations.put("/documents/{identifier}")
def revise_document(identifier: int, body: DocumentInput, db: Db, user: Actor) -> dict[str, object]:
    result = document(db, identifier)
    require(user, "admin", "manager", "sales" if result.kind == "quote" else "finance")
    check_owner(user, result.author_id)
    editable = {"draft", "rejected"} | ({"approved", "sent", "declined"} if result.kind == "quote" else set())
    if result.status not in editable:
        raise HTTPException(409, "This document cannot be revised in its current state")
    return document_data(db, make_document(db, body, user, result))


@mutations.post("/documents/{identifier}/actions")
def document_action(identifier: int, body: ActionInput, db: Db, user: Actor) -> dict[str, object]:
    result = document(db, identifier)
    require(
        user,
        "admin",
        "manager",
        "finance" if body.action == "convert" else "sales" if result.kind == "quote" else "finance",
    )
    check_owner(user, result.author_id)
    if body.action == "submit" and result.status in {"draft", "rejected"}:
        config = settings(db)
        requires_approval = (
            result.kind != "quote"
            or result.total >= config.approval_threshold
            or Decimal(result.discount_rate) > Decimal(config.discount_threshold)
        )
        if requires_approval:
            result.status = "pending"
            db.add(
                Approval(
                    document_id=result.id,
                    revision=result.revision,
                    requested_by=user.id,
                    snapshot=document_data(db, result),
                )
            )
        else:
            result.status = "approved"
            audit(db, user, "Quote approved by workspace rules", result.customer_id, result.number)
    elif body.action == "send" and result.kind == "quote" and result.status == "approved":
        result.status = "sent"
    elif body.action in {"accept", "decline"} and result.kind == "quote" and result.status == "sent":
        if result.due < str(date.today()) and body.action == "accept":
            raise HTTPException(409, "Quote has expired. Create a new quote with current terms.")
        result.status = "accepted" if body.action == "accept" else "declined"
    elif body.action == "convert" and result.kind == "quote" and result.status == "accepted":
        require(user, "admin", "manager", "finance")
        if db.scalar(select(Document.id).where(Document.quote_id == result.id)):
            raise HTTPException(409, "This quote already has an invoice")
        client = customer(db, result.customer_id)
        converted = make_document(
            db,
            DocumentInput(
                kind="invoice",
                customer_id=result.customer_id,
                date=date.today(),
                due=date.today() + timedelta(days=client.payment_days),
                items=[ItemInput.model_validate(value) for value in result.items],
                discount_rate=Decimal(result.discount_rate),
                tax_rate=Decimal(result.tax_rate),
                terms=result.terms,
            ),
            user,
        )
        converted.quote_id = result.id
        converted.snapshot = dict(result.snapshot)
        audit(
            db,
            user,
            "Quote converted to invoice",
            result.customer_id,
            f"{result.number} → {converted.number}",
        )
        return document_data(db, converted)
    elif body.action == "cancel" and result.status in {"draft", "pending", "rejected", "approved", "sent"}:
        result.status = "cancelled"
        for approval in db.scalars(
            select(Approval).where(Approval.document_id == result.id, Approval.status == "pending")
        ):
            approval.status = "cancelled"
    else:
        raise HTTPException(409, "This action is not available in the current document state")
    audit(db, user, f"{result.number}: {body.action}", result.customer_id)
    db.flush()
    return document_data(db, result)


@api.get("/approvals")
def approvals(db: Db, user: Actor) -> list[dict[str, object]]:
    query = select(Approval).order_by(Approval.id.desc())
    if user.role not in {"admin", "manager"}:
        query = query.where(Approval.requested_by == user.id)
    results = []
    for value in db.scalars(query):
        submitter = db.get(User, value.requested_by)
        results.append(
            dict(
                jsonable_encoder(value),
                document=value.snapshot,
                requester=submitter.name if submitter else "Unknown",
            )
        )
    return results


@mutations.post("/approvals/{identifier}/decision")
def decide_approval(identifier: int, body: DecisionInput, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager")
    result = db.get(Approval, identifier)
    if not result:
        raise HTTPException(404, "Approval not found")
    source = document(db, result.document_id)
    if result.status != "pending" or source.status != "pending" or source.revision != result.revision:
        raise HTTPException(409, "Approval has already been processed or the document changed")
    if user.id in {source.author_id, result.requested_by}:
        raise HTTPException(
            403, "The document author cannot approve their own document. Assign another reviewer."
        )
    if body.decision == "rejected" and not body.comment:
        raise HTTPException(400, "Explain why the document is rejected")
    if body.decision == "approved":
        post_document(db, source, user)
    else:
        source.status = "rejected"
    result.status, result.reviewer_id, result.comment = body.decision, user.id, body.comment
    audit(db, user, f"{source.number}: {body.decision}", source.customer_id, body.comment)
    db.flush()
    return document_data(db, source)


def allocate(db: Db, payment: Payment, entries: list[AllocationInput]) -> int:
    seen: set[int] = set()
    allocated = 0
    for value in entries:
        if value.invoice_id in seen:
            raise HTTPException(400, "An invoice may appear only once in an allocation")
        seen.add(value.invoice_id)
        invoice = document(db, value.invoice_id)
        amount = minor(value.amount, settings(db).decimals)
        if (
            amount <= 0
            or invoice.kind != "invoice"
            or invoice.status != "issued"
            or invoice.customer_id != payment.customer_id
        ):
            raise HTTPException(400, "Allocate a positive amount to an issued invoice for this customer")
        if amount > invoice_balance(db, invoice):
            raise HTTPException(409, "Allocation exceeds the invoice balance")
        allocated += amount
        db.add(Allocation(payment_id=payment.id, invoice_id=invoice.id, amount=amount))
    return allocated


@api.get("/payments")
def payments(db: Db, user: Actor) -> list[dict[str, object]]:
    require(user, "admin", "manager", "finance")
    precision = settings(db).decimals
    results = []
    for value in db.scalars(select(Payment).order_by(Payment.id.desc())):
        allocations = list(db.scalars(select(Allocation).where(Allocation.payment_id == value.id)))
        results.append(
            {
                "id": value.id,
                "customer_id": value.customer_id,
                "amount": money(value.amount, precision),
                "date": value.date,
                "reference": value.reference,
                "method": value.method,
                "unallocated": money(value.amount - sum(entry.amount for entry in allocations), precision),
                "allocations": [
                    {"invoice_id": entry.invoice_id, "amount": money(entry.amount, precision)}
                    for entry in allocations
                ],
                "accounting": accounting_for(db, "payment", value.id),
            }
        )
    return results


@mutations.post("/payments", status_code=201)
def record_payment(body: PaymentInput, db: Db, user: Actor) -> dict[str, object]:
    require(user, "admin", "manager", "finance")
    precision = settings(db).decimals
    amount = minor(body.amount, precision)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "customer_id": body.customer_id,
                "created_by": user.id,
                "amount": amount,
                "date": str(body.date),
                "reference": body.reference,
                "method": body.method,
                "allocations": sorted(
                    (value.invoice_id, minor(value.amount, precision)) for value in body.allocations
                ),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    existing = db.scalar(select(Payment).where(Payment.idempotency_key == body.idempotency_key))
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(409, "This request key was already used for a different payment")
        return {"id": existing.id, "amount": money(existing.amount, precision)}
    customer(db, body.customer_id)
    if body.date > date.today():
        raise HTTPException(400, "Payment date cannot be in the future")
    if amount <= 0:
        raise HTTPException(400, "Payment is below the currency's smallest unit")
    result = Payment(
        customer_id=body.customer_id,
        amount=amount,
        date=str(body.date),
        reference=body.reference,
        method=body.method,
        idempotency_key=body.idempotency_key,
        request_fingerprint=fingerprint,
        created_by=user.id,
    )
    db.add(result)
    db.flush()
    if allocate(db, result, body.allocations) > amount:
        raise HTTPException(400, "Allocations exceed the payment amount")
    entry = LedgerEntry(
        customer_id=body.customer_id,
        payment_id=result.id,
        date=result.date,
        description=f"Payment · {body.reference}",
        debit=0,
        credit=amount,
    )
    db.add(entry)
    db.flush()
    enqueue_for_ledger(db, entry)
    audit(
        db,
        user,
        "Payment recorded",
        body.customer_id,
        f"{body.reference}: {money(amount, settings(db).decimals)}",
    )
    return {"id": result.id, "amount": money(amount, settings(db).decimals)}


@mutations.post("/payments/{identifier}/allocate")
def allocate_payment(identifier: int, body: list[AllocationInput], db: Db, user: Actor) -> dict[str, bool]:
    require(user, "admin", "manager", "finance")
    payment = db.get(Payment, identifier)
    if not payment:
        raise HTTPException(404, "Payment not found")
    available = payment.amount - (
        db.scalar(
            select(func.coalesce(func.sum(Allocation.amount), 0)).where(Allocation.payment_id == identifier)
        )
        or 0
    )
    credit = unallocated_credit(db, payment.customer_id)
    if allocate(db, payment, body) > min(available, credit):
        raise HTTPException(409, "Allocation exceeds available customer credit")
    audit(db, user, "Payment allocated", payment.customer_id, payment.reference)
    return {"ok": True}


def ledger_data(db: Db, identifier: int, start: date | None, end: date | None) -> dict[str, object]:
    client = customer(db, identifier)
    precision = settings(db).decimals
    opening, balance = 0, 0
    entries = []
    for entry in db.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.customer_id == identifier)
        .order_by(LedgerEntry.date, LedgerEntry.id)
    ):
        if end and entry.date > str(end):
            continue
        balance += entry.debit - entry.credit
        if start and entry.date < str(start):
            opening = balance
            continue
        entries.append(
            {
                "id": entry.id,
                "date": entry.date,
                "description": entry.description,
                "debit": money(entry.debit, precision),
                "credit": money(entry.credit, precision),
                "balance": money(balance, precision),
            }
        )
    return {
        "customer": client.name,
        "opening": money(opening, precision),
        "balance": money(balance, precision),
        "unallocated": money(unallocated_credit(db, identifier), precision),
        "entries": entries,
    }


@api.get("/customers/{identifier}/ledger")
def ledger(
    identifier: int, db: Db, user: Actor, start: date | None = None, end: date | None = None
) -> dict[str, object]:
    if start and end and start > end:
        raise HTTPException(400, "Start date must precede end date")
    return ledger_data(db, identifier, start, end)


@api.get("/customers/{identifier}/statement")
def statement(
    identifier: int,
    db: Db,
    user: Actor,
    format: str = "pdf",
    start: date | None = None,
    end: date | None = None,
) -> Response:
    data = ledger(identifier, db, user, start, end)
    precision = settings(db)
    entries = data["entries"]
    assert isinstance(entries, list)
    rows = [["Date", "Description", "Debit", "Credit", "Balance"]]
    rows.append([str(start or ""), "Opening balance", "", "", str(data["opening"])])
    rows.extend(
        [
            [
                str(entry["date"]),
                str(entry["description"]),
                str(entry["debit"]),
                str(entry["credit"]),
                str(entry["balance"]),
            ]
            for entry in entries
        ]
    )
    if format == "csv":
        return csv_response(rows, f"customer-{identifier}-statement.csv")
    lines = [
        precision.company,
        str(data["customer"]),
        f"Period: {start or 'All dates'} to {end or 'Today'}",
        f"Currency: {precision.currency}",
        "",
        *[" | ".join(row) for row in rows],
        "",
        f"Closing balance: {data['balance']} {precision.currency}",
    ]
    return Response(
        make_pdf("Customer account statement", lines),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="customer-{identifier}-statement.pdf"'},
    )


@api.get("/documents/{identifier}/pdf")
def document_pdf(identifier: int, db: Db, user: Actor) -> Response:
    value = document(db, identifier)
    check_owner(user, value.author_id)
    data = document_data(db, value)
    lines = [
        value.snapshot.get("company", ""),
        value.snapshot.get("company_address", ""),
        f"Company tax ID: {value.snapshot.get('company_tax_id', '')}",
        "",
        f"Customer: {data['customer']}",
        value.snapshot.get("address", ""),
        f"Customer tax ID: {value.snapshot.get('tax_id', '')}",
        f"Date: {value.date} | Due / valid until: {value.due}",
        f"Status: {value.status.upper()} | Revision {value.revision}",
        "",
    ]
    for item in value.items:
        lines.append(f"{item['description']} | {item['quantity']} x {item['unit_price']} {value.currency}")
    lines.extend(
        [
            "",
            f"Subtotal: {data['subtotal']}",
            f"Discount ({value.discount_rate}%): {data['discount']}",
            f"Tax ({value.tax_rate}%): {data['tax']}",
            f"Total: {data['total']} {value.currency}",
            "",
            value.terms,
            value.reason,
        ]
    )
    return Response(
        make_pdf(f"{value.kind.title()} {value.number}", lines),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{value.number}.pdf"'},
    )


@api.get("/reports")
def reports(db: Db, user: Actor, start: date | None = None, end: date | None = None) -> dict[str, object]:
    require(user, "admin", "manager", "finance")
    if start and end and start > end:
        raise HTTPException(400, "Start date must precede end date")
    config = settings(db)
    issued = list(db.scalars(select(Document).where(Document.status == "issued")))
    period = [
        value
        for value in issued
        if (not start or value.date >= str(start)) and (not end or value.date <= str(end))
    ]
    payment_rows = list(db.scalars(select(Payment)))
    collected = sum(
        value.amount
        for value in payment_rows
        if (not start or value.date >= str(start)) and (not end or value.date <= str(end))
    )
    refunded = sum(value.total for value in period if value.kind == "refund")
    invoiced = sum(value.total for value in period if value.kind == "invoice")
    credited = sum(value.total for value in period if value.kind == "credit")
    ageing = [0, 0, 0, 0, 0]
    open_invoices = []
    for value in issued:
        if value.kind != "invoice":
            continue
        balance = invoice_balance(db, value)
        if balance <= 0:
            continue
        days = (date.today() - date.fromisoformat(value.due)).days
        bucket = 0 if days <= 0 else 1 if days <= 30 else 2 if days <= 60 else 3 if days <= 90 else 4
        ageing[bucket] += balance
        open_invoices.append(document_data(db, value))
    accounts = [
        {
            "id": value.id,
            "name": value.name,
            "balance": money(customer_balance(db, value.id), config.decimals),
            "unallocated": money(unallocated_credit(db, value.id), config.decimals),
        }
        for value in db.scalars(select(Customer).order_by(Customer.name))
    ]
    salespeople = []
    for member in db.scalars(select(User)):
        deals = list(db.scalars(select(Opportunity).where(Opportunity.owner_id == member.id)))
        wins = [value for value in deals if value.stage == "Won"]
        salespeople.append(
            {
                "name": member.name,
                "deals": len(deals),
                "won": len(wins),
                "won_value": money(sum(value.value for value in wins), config.decimals),
            }
        )
    quote_count = (
        db.scalar(
            select(func.count(Document.id)).where(Document.kind == "quote", Document.status != "cancelled")
        )
        or 0
    )
    converted = (
        db.scalar(
            select(func.count(Document.id)).where(
                Document.quote_id.is_not(None), Document.status != "cancelled"
            )
        )
        or 0
    )
    months = sorted(
        {value.date[:7] for value in period}
        | {
            value.date[:7]
            for value in payment_rows
            if (not start or value.date >= str(start)) and (not end or value.date <= str(end))
        }
    )[-6:]
    trend = [
        {
            "month": month,
            "invoiced": money(
                sum(
                    value.total * (-1 if value.kind == "credit" else 1)
                    for value in period
                    if value.date.startswith(month) and value.kind in {"invoice", "credit"}
                ),
                config.decimals,
            ),
            "collected": money(
                sum(
                    value.amount
                    for value in payment_rows
                    if value.date.startswith(month)
                    and (not start or value.date >= str(start))
                    and (not end or value.date <= str(end))
                ),
                config.decimals,
            ),
        }
        for month in months
    ]
    return {
        "invoiced": money(invoiced, config.decimals),
        "credited": money(credited, config.decimals),
        "net_sales": money(invoiced - credited, config.decimals),
        "collected": money(collected, config.decimals),
        "refunded": money(refunded, config.decimals),
        "net_collections": money(collected - refunded, config.decimals),
        "outstanding": money(sum(ageing), config.decimals),
        "ageing": [money(value, config.decimals) for value in ageing],
        "accounts": accounts,
        "salespeople": salespeople,
        "open_invoices": open_invoices,
        "quote_conversion": round(converted / quote_count * 100, 1) if quote_count else 0,
        "trend": trend,
        "receivables_as_of": str(date.today()),
    }


@api.get("/exports/reports")
def export_reports(db: Db, user: Actor, start: date | None = None, end: date | None = None) -> Response:
    data = reports(db, user, start, end)
    rows = [
        ["Metric", f"Amount ({settings(db).currency})"],
        ["Invoiced sales", str(data["invoiced"])],
        ["Credit notes", str(data["credited"])],
        ["Net sales", str(data["net_sales"])],
        ["Collections", str(data["collected"])],
        ["Refunds", str(data["refunded"])],
        ["Net collections", str(data["net_collections"])],
        ["Current outstanding invoices", str(data["outstanding"])],
        [],
        ["Customer", "Current ledger balance", "Unallocated payment credit"],
    ]
    accounts = data["accounts"]
    assert isinstance(accounts, list)
    rows.extend(
        [[str(value["name"]), str(value["balance"]), str(value["unallocated"])] for value in accounts]
    )
    return csv_response(rows, "crm-report.csv")


@api.get("/dashboard")
def dashboard(db: Db, user: Actor) -> dict[str, object]:
    config = settings(db)
    deals = opportunities(db, user)
    task_rows = tasks(db, user)
    pipeline = sum(Decimal(str(value["value"])) for value in deals if value["stage"] not in {"Won", "Lost"})
    financial = reports(db, user) if user.role != "sales" else None
    activities = select(Audit).order_by(Audit.id.desc()).limit(12)
    if user.role == "sales":
        activities = activities.where(Audit.actor_id == user.id)
    return {
        "customers": db.scalar(select(func.count(Customer.id))),
        "pipeline": str(pipeline),
        "overdue_tasks": sum(
            not value["done"] and str(value["due"]) < str(date.today()) for value in task_rows
        ),
        "pending_approvals": len([value for value in approvals(db, user) if value["status"] == "pending"]),
        "financial": financial,
        "activities": jsonable_encoder(list(db.scalars(activities))),
        "company": config.company,
    }


@api.get("/audit")
def audit_history(
    db: Db, user: Actor, limit: Annotated[int, Query(ge=1, le=1000)] = 200
) -> list[dict[str, object]]:
    require(user, "admin", "manager")
    return jsonable_encoder(list(db.scalars(select(Audit).order_by(Audit.id.desc()).limit(limit))))


app.include_router(provider_router)
app.include_router(api)
app.include_router(mutations)
frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend.is_dir():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
