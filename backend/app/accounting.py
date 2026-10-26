from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Document, ErpLink, ErpPosting, LedgerEntry
from .services import money

ACCOUNT_TYPES = {
    "receivable": {"ASSET"},
    "revenue": {"REVENUE"},
    "receipts": {"ASSET"},
    "returns": {"REVENUE"},
    "opening": {"EQUITY"},
    "adjustments": {"EQUITY", "REVENUE", "EXPENSE"},
    "tax": {"LIABILITY"},
}


def pending_entries(db: Session, link: ErpLink) -> list[LedgerEntry]:
    return list(
        db.scalars(
            select(LedgerEntry)
            .outerjoin(ErpPosting, ErpPosting.ledger_id == LedgerEntry.id)
            .where(LedgerEntry.id > link.cutoff_ledger_id, ErpPosting.ledger_id.is_(None))
            .order_by(LedgerEntry.id)
            .limit(10)
        )
    )


def journal_payload(db: Session, link: ErpLink, entry: LedgerEntry) -> dict[str, object]:
    lines: list[dict[str, object]] = []

    def line(account: str, amount: int, debit: bool) -> None:
        if not amount:
            return
        account_id = link.account_ids.get(account)
        if not account_id:
            raise HTTPException(409, f"The {account} posting account has not been mapped")
        lines.append(
            {
                "accountId": account_id,
                "branchId": None,
                "description": entry.description[:500],
                "debit": money(amount if debit else 0, 3),
                "credit": money(0 if debit else amount, 3),
            }
        )

    amount = entry.debit or entry.credit
    if amount <= 0 or (entry.debit and entry.credit):
        raise HTTPException(409, "The customer ledger entry cannot be posted")
    line("receivable", amount, entry.debit > 0)
    source = db.get(Document, entry.document_id) if entry.document_id else None
    reference = source.number if source else f"Payment {entry.payment_id}"
    if entry.payment_id:
        line("receipts", amount, True)
    elif source and source.kind == "invoice":
        if source.currency != "KWD" or source.decimals != 3:
            raise HTTPException(409, "Only KWD documents can be posted to the linked company")
        line("revenue", source.total - source.tax, False)
        line("tax", source.tax, False)
    elif source and source.kind == "credit":
        invoice = db.get(Document, source.invoice_id)
        if not invoice or invoice.currency != "KWD" or invoice.decimals != 3:
            raise HTTPException(409, "The source invoice does not match the linked company currency")
        prior_credit = (
            db.scalar(
                select(func.coalesce(func.sum(Document.total), 0))
                .join(LedgerEntry, LedgerEntry.document_id == Document.id)
                .where(
                    Document.invoice_id == invoice.id, Document.kind == "credit", LedgerEntry.id < entry.id
                )
            )
            or 0
        )

        def credited_tax(total: int) -> int:
            return int(
                (Decimal(invoice.tax) * Decimal(total) / Decimal(invoice.total)).quantize(
                    Decimal(1), rounding=ROUND_HALF_UP
                )
            )

        tax = credited_tax(prior_credit + source.total) - credited_tax(prior_credit)
        line("returns", source.total - tax, True)
        line("tax", tax, True)
    elif source and source.kind == "refund":
        line("receipts", amount, False)
    elif source and source.kind in {"opening", "adjustment"}:
        line("opening" if source.kind == "opening" else "adjustments", amount, entry.credit > 0)
    else:
        raise HTTPException(409, "This customer ledger transaction needs manual reconciliation")
    return {
        "externalId": f"{link.workspace_id}:ledger:{entry.id}",
        "currency": "KWD",
        "date": entry.date,
        "description": entry.description[:500],
        "reference": reference[:100],
        "lines": lines,
    }
