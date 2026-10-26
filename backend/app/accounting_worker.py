import argparse
import logging
import os
import random
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Engine, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from .control import Workspace, WorkspaceCredential, control_sessions, provider_audit
from .erp import erp_base
from .models import AccountingOutbox, AccountingState, Audit, LedgerEntry
from .outbox import canonical_json
from .secretbox import decrypt
from .tenancy import workspace_sessions

log = logging.getLogger("crm.accounting_worker")
BATCH = 10
LEASE_SECONDS = 120
PAUSE_PROBE = timedelta(minutes=10)
BLOCKED_RECHECK = timedelta(hours=1)
ATTENTION_ATTEMPTS = 12
CLAIMABLE = ("pending", "retrying", "blocked")


@dataclass
class Claim:
    id: int
    event_id: str
    attempts: int
    payload: dict[str, Any]


@dataclass
class Outcome:
    status: str
    http_status: int | None = None
    code: str | None = None
    message: str | None = None
    journal_id: str | None = None
    journal_number: int | None = None
    delay: timedelta | None = None
    pause: str | None = None


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def claim(
    sessions: sessionmaker[Session], worker: str, limit: int = BATCH, probe: bool = False
) -> list[Claim]:
    engine = sessions.kw["bind"]
    assert isinstance(engine, Engine)
    now = utcnow()
    statuses = CLAIMABLE + ("held",) if probe else CLAIMABLE
    params = {
        "worker": worker,
        "now": now,
        "lease": now + timedelta(seconds=LEASE_SECONDS),
        "limit": 1 if probe else limit,
    }
    status_list = ",".join(f"'{value}'" for value in statuses)
    candidates = (
        "SELECT id FROM crm_accounting_outbox WHERE "
        f"(status IN ({status_list}) AND next_attempt_at <= :now) "
        "OR (status = 'in_progress' AND lease_expires_at < :now) ORDER BY id LIMIT :limit"
    )
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            rows = connection.execute(
                text(
                    "UPDATE crm_accounting_outbox SET status='in_progress', lease_owner=:worker, "
                    "lease_expires_at=:lease, attempts=attempts+1, updated_at=:now "
                    f"WHERE id IN ({candidates} FOR UPDATE SKIP LOCKED) "
                    "RETURNING id, event_id, attempts, payload"
                ),
                params,
            ).all()
    else:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                ids = [row[0] for row in connection.execute(text(candidates), params)]
                rows = []
                for identifier in ids:
                    connection.execute(
                        text(
                            "UPDATE crm_accounting_outbox SET status='in_progress', lease_owner=:worker, "
                            "lease_expires_at=:lease, attempts=attempts+1, updated_at=:now WHERE id=:id"
                        ),
                        {**params, "id": identifier},
                    )
                    rows.append(
                        connection.execute(
                            text(
                                "SELECT id, event_id, attempts, payload FROM crm_accounting_outbox WHERE id=:id"
                            ),
                            {"id": identifier},
                        ).one()
                    )
                connection.exec_driver_sql("COMMIT")
            except Exception:
                connection.exec_driver_sql("ROLLBACK")
                raise
    result = []
    for row in sorted(rows, key=lambda value: value[0]):
        payload = row[3]
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)
        result.append(Claim(id=row[0], event_id=row[1], attempts=row[2], payload=payload))
    return result


def error_detail(response: httpx.Response) -> tuple[str | None, str | None]:
    try:
        body = response.json()
    except ValueError:
        return None, None
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, None
    code, message = error.get("code"), error.get("message")
    return (str(code)[:60] if code else None, str(message)[:500] if message else None)


def backoff(attempts: int, retry_after: str | None) -> timedelta:
    seconds = min(30 * 2 ** max(attempts - 1, 0), 3600) * random.uniform(0.8, 1.2)
    if retry_after and retry_after.isdigit():
        seconds = max(seconds, min(int(retry_after), 3600))
    return timedelta(seconds=seconds)


def classify(response: httpx.Response, attempts: int) -> Outcome:
    status = response.status_code
    code, message = error_detail(response)
    if status in {200, 201}:
        try:
            body = response.json()
            journal_id = str(uuid.UUID(str(body["journalId"])))
            number = body["number"]
            if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
                raise ValueError
            return Outcome("posted", status, journal_id=journal_id, journal_number=number)
        except (ValueError, KeyError, TypeError):
            return Outcome(
                "retrying",
                status,
                "ERP_BAD_RESPONSE",
                "ERP returned an invalid journal link",
                delay=backoff(attempts, None),
            )
    if status == 409 and code == "CRM_EVENT_CONFLICT":
        return Outcome("conflict", status, code, message or "The ERP holds different content for this event")
    if status == 401:
        return Outcome(
            "held",
            status,
            "CREDENTIAL_REJECTED",
            "ERP rejected the workspace credential",
            pause="CREDENTIAL_REJECTED",
        )
    if status == 403:
        reason = "ERP_POSTING_DISABLED" if code == "POSTING_DISABLED" else "ERP_FORBIDDEN"
        return Outcome(
            "held", status, code or "FORBIDDEN", message or "ERP posting is disabled", pause=reason
        )
    if status == 429 or status >= 500:
        return Outcome(
            "retrying",
            status,
            code or f"HTTP_{status}",
            message or "ERP is temporarily unavailable",
            delay=backoff(attempts, response.headers.get("retry-after")),
        )
    return Outcome(
        "blocked", status, code or f"HTTP_{status}", message or "ERP refused the event", delay=BLOCKED_RECHECK
    )


def send(client: httpx.Client, base: str, workspace_id: str, token: str, row: Claim) -> Outcome:
    try:
        response = client.post(
            f"{base}/api/v1/integrations/crm/events",
            content=canonical_json(row.payload).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "X-Crm-Workspace-Id": workspace_id,
                "Idempotency-Key": row.event_id,
                "Content-Type": "application/json",
            },
        )
    except httpx.HTTPError as exc:
        return Outcome(
            "retrying", None, "ERP_UNREACHABLE", type(exc).__name__, delay=backoff(row.attempts, None)
        )
    return classify(response, row.attempts)


def apply(sessions: sessionmaker[Session], worker: str, row: Claim, outcome: Outcome) -> None:
    now = utcnow()
    with sessions() as db:
        values: dict[str, Any] = {
            "status": outcome.status,
            "last_http_status": outcome.http_status,
            "lease_owner": None,
            "lease_expires_at": None,
            "updated_at": now,
        }
        guard = [AccountingOutbox.id == row.id, AccountingOutbox.status != "posted"]
        if outcome.status == "posted":
            values.update(
                erp_journal_id=outcome.journal_id,
                erp_journal_number=outcome.journal_number,
                posted_at=now,
                last_error_code=None,
                last_error_message=None,
            )
        else:
            guard.append(AccountingOutbox.lease_owner == worker)
            message = outcome.message
            if outcome.status == "retrying" and row.attempts >= ATTENTION_ATTEMPTS:
                message = f"Needs attention after {row.attempts} attempts: {message or ''}"[:500]
            values.update(last_error_code=outcome.code, last_error_message=message)
            if outcome.delay is not None:
                values["next_attempt_at"] = now + outcome.delay
            if outcome.status == "held":
                values["attempts"] = AccountingOutbox.attempts - 1
                values["next_attempt_at"] = now
        changed = db.execute(update(AccountingOutbox).where(*guard).values(**values)).rowcount
        if changed and outcome.status == "posted":
            ledger = db.get(
                LedgerEntry,
                db.scalar(select(AccountingOutbox.ledger_id).where(AccountingOutbox.id == row.id)),
            )
            db.add(
                Audit(
                    actor_id=None,
                    customer_id=ledger.customer_id if ledger else None,
                    action="ERP journal linked",
                    detail=f"{row.event_id} → Journal {outcome.journal_number}",
                )
            )
        state = db.get(AccountingState, 1)
        if state and outcome.pause:
            state.paused_reason = outcome.pause
            state.paused_at = now
        elif state and state.paused_reason and outcome.status != "held":
            state.paused_reason = None
            state.paused_at = None
            db.execute(
                update(AccountingOutbox)
                .where(AccountingOutbox.status == "held")
                .values(status="pending", next_attempt_at=now, updated_at=now)
            )
        db.commit()


def release(sessions: sessionmaker[Session], worker: str, rows: list[Claim]) -> None:
    if not rows:
        return
    with sessions() as db:
        db.execute(
            update(AccountingOutbox)
            .where(AccountingOutbox.id.in_([row.id for row in rows]), AccountingOutbox.lease_owner == worker)
            .values(
                status="pending",
                attempts=AccountingOutbox.attempts - 1,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=utcnow(),
            )
        )
        db.commit()


def process_workspace(
    client: httpx.Client,
    base: str,
    workspace_id: str,
    token: str,
    sessions: sessionmaker[Session],
    worker: str,
) -> dict[str, int]:
    with sessions() as db:
        state = db.get(AccountingState, 1)
        if not state or not state.enabled:
            return {}
        probe = bool(state.paused_reason)
        paused_at = aware(state.paused_at)
        if probe and paused_at and utcnow() < paused_at + PAUSE_PROBE:
            return {}
    stats: dict[str, int] = {}
    rows = claim(sessions, worker, probe=probe)
    for index, row in enumerate(rows):
        outcome = send(client, base, workspace_id, token, row)
        apply(sessions, worker, row, outcome)
        stats[outcome.status] = stats.get(outcome.status, 0) + 1
        log.info(
            "workspace=%s event=%s status=%s code=%s",
            workspace_id,
            row.event_id,
            outcome.status,
            outcome.code,
        )
        if outcome.pause:
            release(sessions, worker, rows[index + 1 :])
            if outcome.pause == "CREDENTIAL_REJECTED":
                with control_sessions()() as control:
                    provider_audit(control, workspace_id, "accounting-worker", "credential.rejected")
                    control.commit()
                log.warning(
                    "workspace=%s ERP rejected the service credential; provider action needed", workspace_id
                )
            break
    return stats


def run_once(client: httpx.Client, worker: str) -> dict[str, dict[str, int]]:
    base = erp_base()
    with control_sessions()() as control:
        targets = [
            (workspace, credential.token_enc)
            for workspace, credential in control.execute(
                select(Workspace, WorkspaceCredential)
                .join(WorkspaceCredential, WorkspaceCredential.workspace_id == Workspace.id)
                .where(Workspace.status == "active", WorkspaceCredential.status == "active")
                .order_by(Workspace.slug)
            )
        ]
    results = {}
    for workspace, token_enc in targets:
        try:
            results[workspace.slug] = process_workspace(
                client, base, workspace.id, decrypt(token_enc), workspace_sessions(workspace), worker
            )
        except Exception as exc:
            log.error("workspace=%s worker cycle failed: %s", workspace.slug, type(exc).__name__)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.accounting_worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if os.environ.get("CRM_TENANCY") != "hosted":
        print("The accounting worker only runs with CRM_TENANCY=hosted")
        return 2
    if os.environ.get("CRM_ACCOUNTING_WORKER_ENABLED") != "true":
        print("CRM_ACCOUNTING_WORKER_ENABLED is not true; posting stays disabled")
        return 0
    if os.environ.get("CRM_ENV") == "production":
        print("Automatic ERP posting is not approved for production in this release")
        return 2
    worker = worker_name()
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        while True:
            run_once(client, worker)
            if args.once:
                return 0
            time.sleep(float(os.environ.get("CRM_ACCOUNTING_POLL_SECONDS", "5")))


if __name__ == "__main__":
    raise SystemExit(main())
