import hashlib
import json
import re
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    AccountingOutbox,
    AccountingState,
    Customer,
    Document,
    LedgerEntry,
    Payment,
    Settings,
)

DOCUMENT_EVENTS = {"invoice": "invoice.issued", "credit": "credit_note.issued", "refund": "refund.paid"}
MANUAL_EVENTS = {"opening": "opening.recorded", "adjustment": "adjustment.recorded"}
EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9:._-]{1,100}$")


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def kwd(value: int) -> str:
    return f"{Decimal(value) / Decimal(1000):.3f}"


def credit_note_tax(db: Session, credit: Document, invoice: Document, ledger_id: int) -> int:
    prior_credit = (
        db.scalar(
            select(func.coalesce(func.sum(Document.total), 0))
            .join(LedgerEntry, LedgerEntry.document_id == Document.id)
            .where(Document.invoice_id == invoice.id, Document.kind == "credit", LedgerEntry.id < ledger_id)
        )
        or 0
    )

    def credited_tax(total: int) -> int:
        if not invoice.total:
            return 0
        return int(
            (Decimal(invoice.tax) * Decimal(total) / Decimal(invoice.total)).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            )
        )

    return credited_tax(prior_credit + credit.total) - credited_tax(prior_credit)


def event_id(workspace_id: str, source_type: str, source_id: int, event_type: str) -> str:
    value = f"crmws:{workspace_id}:{source_type}:{source_id}:{event_type}"
    if not EVENT_ID_PATTERN.match(value):
        raise ValueError("Accounting event id is not valid")
    return value


def enqueue_for_ledger(db: Session, entry: LedgerEntry) -> AccountingOutbox | None:
    """Freeze an accounting event for a newly finalized ledger row, inside the caller's transaction."""
    state = db.get(AccountingState, 1)
    if not state or not state.enabled or not state.workspace_id:
        return None
    db.flush()
    if entry.id <= state.cutoff_ledger_id:
        return None
    if db.scalar(select(AccountingOutbox.id).where(AccountingOutbox.ledger_id == entry.id)):
        return None
    customer = db.get(Customer, entry.customer_id)
    assert customer is not None
    document = db.get(Document, entry.document_id) if entry.document_id else None
    payment = db.get(Payment, entry.payment_id) if entry.payment_id else None
    status, code, message = "pending", None, None
    invoice_reference = None
    if payment:
        source_type, source_id, event_type = "payment", payment.id, "payment.received"
        config = db.get(Settings, 1)
        currency, decimals = (config.currency, config.decimals) if config else ("", 0)
        total, tax = payment.amount, 0
        reference = (payment.reference or f"PAY-{payment.id}")[:100]
    elif document and document.kind in DOCUMENT_EVENTS:
        source_type, source_id, event_type = "document", document.id, DOCUMENT_EVENTS[document.kind]
        currency, decimals, total, tax = document.currency, document.decimals, document.total, 0
        reference = document.number[:100]
        if document.kind == "invoice":
            tax = document.tax
        elif document.kind == "credit":
            invoice = db.get(Document, document.invoice_id) if document.invoice_id else None
            if invoice is None:
                status, code, message = "blocked", "SOURCE_INVOICE_MISSING", "The credited invoice is missing"
            else:
                invoice_reference = invoice.number
                tax = credit_note_tax(db, document, invoice, entry.id)
                if (invoice.currency, invoice.decimals) != (currency, decimals):
                    currency = invoice.currency
    elif document and document.kind in MANUAL_EVENTS:
        source_type, source_id, event_type = "document", document.id, MANUAL_EVENTS[document.kind]
        currency, decimals, total, tax = document.currency, document.decimals, document.total, 0
        reference = document.number[:100]
        status, code = "skipped", "MANUAL_POSTING_REQUIRED"
        message = "Opening balances and adjustments are posted manually by finance in the ERP"
    else:
        return None
    if status != "skipped" and (currency != "KWD" or decimals != 3):
        status, code, message = (
            "blocked",
            "CURRENCY_NOT_SUPPORTED",
            "Only KWD amounts with 3 decimals are posted",
        )
    if status == "pending" and total <= 0:
        status, code, message = "blocked", "VALIDATION", "The accounting amount must be greater than zero"
    identifier = event_id(state.workspace_id, source_type, source_id, event_type)
    payload: dict[str, object] = {
        "eventId": identifier,
        "type": event_type,
        "currency": "KWD",
        "date": entry.date.isoformat() if isinstance(entry.date, date) else str(entry.date),
        "reference": reference,
        "description": (entry.description or reference)[:500],
        "customer": {"ref": str(customer.id), "name": customer.name[:200], "kind": customer.kind},
        "amounts": {"net": kwd(total - tax), "tax": kwd(tax), "total": kwd(total)},
        "source": {
            "documentId": document.id if document else None,
            "paymentId": payment.id if payment else None,
            "invoiceReference": invoice_reference,
        },
    }
    row = AccountingOutbox(
        event_id=identifier,
        event_type=event_type,
        source_type=source_type,
        source_id=source_id,
        ledger_id=entry.id,
        payload=payload,
        payload_hash=payload_hash(payload),
        status=status,
        attempts=0,
        next_attempt_at=datetime.now(UTC),
        last_error_code=code,
        last_error_message=message,
    )
    db.add(row)
    db.flush()
    return row


def accounting_view(row: AccountingOutbox | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "status": row.status,
        "erpJournalNumber": row.erp_journal_number,
        "lastErrorMessage": row.last_error_message,
    }


def accounting_for(db: Session, source_type: str, source_id: int) -> dict[str, object] | None:
    return accounting_view(
        db.scalar(
            select(AccountingOutbox).where(
                AccountingOutbox.source_type == source_type, AccountingOutbox.source_id == source_id
            )
        )
    )
