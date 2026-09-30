from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    Allocation,
    Audit,
    Customer,
    Document,
    DocumentRevision,
    LedgerEntry,
    Payment,
    Settings,
    User,
)
from .schemas import DocumentInput


def settings(db: Session) -> Settings:
    result = db.get(Settings, 1)
    assert result is not None
    return result


def minor(value: Decimal, decimals: int) -> int:
    return int((value * Decimal(10) ** decimals).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def money(value: int, decimals: int) -> str:
    return f"{Decimal(value) / Decimal(10) ** decimals:.{decimals}f}"


def audit(db: Session, user: User, action: str, customer_id: int | None = None, detail: str = "") -> None:
    db.add(Audit(actor_id=user.id, customer_id=customer_id, action=action, detail=detail))


def customer(db: Session, identifier: int) -> Customer:
    result = db.get(Customer, identifier)
    if not result:
        raise HTTPException(404, "Customer not found")
    return result


def document(db: Session, identifier: int) -> Document:
    result = db.get(Document, identifier)
    if not result:
        raise HTTPException(404, "Document not found")
    return result


def invoice_balance(db: Session, invoice: Document) -> int:
    allocated = (
        db.scalar(
            select(func.coalesce(func.sum(Allocation.amount), 0)).where(Allocation.invoice_id == invoice.id)
        )
        or 0
    )
    return max(0, invoice.total - allocated - invoice_credit(db, invoice))


def invoice_credit(db: Session, invoice: Document) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(Document.total), 0)).where(
                Document.invoice_id == invoice.id, Document.kind == "credit", Document.status == "issued"
            )
        )
        or 0
    )


def customer_balance(db: Session, identifier: int) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(LedgerEntry.debit - LedgerEntry.credit), 0)).where(
                LedgerEntry.customer_id == identifier
            )
        )
        or 0
    )


def unallocated_credit(db: Session, identifier: int) -> int:
    paid = (
        db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.customer_id == identifier))
        or 0
    )
    allocated = (
        db.scalar(
            select(func.coalesce(func.sum(Allocation.amount), 0))
            .join(Payment)
            .where(Payment.customer_id == identifier)
        )
        or 0
    )
    refunded = (
        db.scalar(
            select(func.coalesce(func.sum(Document.total), 0)).where(
                Document.customer_id == identifier, Document.kind == "refund", Document.status == "issued"
            )
        )
        or 0
    )
    return paid - allocated - refunded


def make_document(db: Session, body: DocumentInput, user: User, existing: Document | None = None) -> Document:
    config = settings(db)
    client = customer(db, body.customer_id)
    if body.due < body.date or body.date > date.today():
        raise HTTPException(400, "Document date must be today or earlier, and due date cannot precede it")
    if existing and (existing.kind != body.kind or existing.customer_id != body.customer_id):
        raise HTTPException(400, "Document type and customer cannot change")
    if body.kind in {"quote", "invoice"}:
        if not body.items:
            raise HTTPException(400, "Add at least one line item")
        if body.invoice_id:
            raise HTTPException(400, "Only credit notes can reference an invoice")
        subtotal = sum(minor(item.quantity * item.unit_price, config.decimals) for item in body.items)
        discount = minor(Decimal(subtotal) * body.discount_rate / 100, 0)
        tax = minor(Decimal(subtotal - discount) * body.tax_rate / 100, 0)
        total = subtotal - discount + tax
        if total <= 0 or total > 10**14:
            raise HTTPException(400, "Document total must be positive and within the supported limit")
    else:
        if not body.reason:
            raise HTTPException(400, "A reason is required for account transactions")
        if body.items or body.discount_rate or body.tax_rate:
            raise HTTPException(400, "Account transactions use an amount, without line items or taxes")
        total = minor(body.amount, config.decimals)
        if total == 0 or (body.kind in {"credit", "refund"} and total < 0):
            raise HTTPException(400, "Enter a valid non-zero amount")
        subtotal, discount, tax = total, 0, 0
        if body.kind == "credit":
            if not body.invoice_id:
                raise HTTPException(400, "Select the invoice for the credit note")
            invoice = document(db, body.invoice_id)
            if invoice.kind != "invoice" or invoice.status != "issued" or invoice.customer_id != client.id:
                raise HTTPException(400, "Select an issued invoice for this customer")
            if total > invoice.total - invoice_credit(db, invoice):
                raise HTTPException(400, "Credit exceeds the invoice's remaining creditable amount")
        elif body.invoice_id:
            raise HTTPException(400, "Only credit notes can reference an invoice")
    if existing:
        result = existing
        db.add(
            DocumentRevision(
                document_id=result.id, revision=result.revision, content=document_data(db, result)
            )
        )
        result.revision += 1
        result.status = "draft"
        result.author_id = user.id
    else:
        identifier = (db.scalar(select(func.max(Document.id))) or 0) + 1
        result = Document(
            id=identifier,
            number=f"{config.number_prefixes[body.kind]}-{identifier:05d}",
            kind=body.kind,
            customer_id=client.id,
            author_id=user.id,
            currency=config.currency,
            decimals=config.decimals,
        )
        db.add(result)
    result.date, result.due = str(body.date), str(body.due)
    result.items = [item.model_dump(mode="json") for item in body.items]
    result.subtotal, result.discount, result.tax, result.total = subtotal, discount, tax, total
    result.discount_rate, result.tax_rate = str(body.discount_rate), str(body.tax_rate)
    result.terms, result.reason, result.invoice_id = body.terms, body.reason, body.invoice_id
    result.snapshot = {
        "customer": client.name,
        "address": client.address,
        "tax_id": client.tax_id,
        "company": config.company,
        "company_address": config.address,
        "company_tax_id": config.tax_id,
    }
    db.flush()
    audit(db, user, f"{result.number} {'revised' if existing else 'created'}", client.id)
    return result


def post_document(db: Session, result: Document, reviewer: User) -> None:
    if result.kind == "quote":
        result.status = "approved"
        return
    if result.kind == "credit":
        assert result.invoice_id is not None
        invoice = document(db, result.invoice_id)
        if result.total > invoice.total - invoice_credit(db, invoice):
            raise HTTPException(409, "Invoice creditable amount changed; revise the credit amount")
        allocations = list(
            db.scalars(
                select(Allocation).where(Allocation.invoice_id == invoice.id).order_by(Allocation.id.desc())
            )
        )
        excess = (
            sum(value.amount for value in allocations)
            + invoice_credit(db, invoice)
            + result.total
            - invoice.total
        )
        for allocation in allocations:
            if excess <= 0:
                break
            released = min(excess, allocation.amount)
            allocation.amount -= released
            excess -= released
            payment = db.get(Payment, allocation.payment_id)
            assert payment is not None
            audit(
                db,
                reviewer,
                "Payment credit released",
                result.customer_id,
                f"{result.number}: {money(released, result.decimals)} released from {payment.reference} / {invoice.number}",
            )
            if allocation.amount == 0:
                db.delete(allocation)
    if result.kind == "refund" and result.total > unallocated_credit(db, result.customer_id):
        raise HTTPException(409, "Refund exceeds unallocated customer credit")
    if result.kind == "opening" and db.scalar(
        select(LedgerEntry.id).where(LedgerEntry.customer_id == result.customer_id).limit(1)
    ):
        raise HTTPException(409, "Opening balances must be the first customer ledger entry")
    signed = -result.total if result.kind == "credit" else result.total
    result.status = "issued"
    db.add(
        LedgerEntry(
            customer_id=result.customer_id,
            document_id=result.id,
            date=result.date,
            description=f"{result.number} · {result.reason or result.kind.title()}",
            debit=max(signed, 0),
            credit=max(-signed, 0),
        )
    )


def document_data(db: Session, result: Document) -> dict[str, object]:
    outstanding = invoice_balance(db, result) if result.kind == "invoice" and result.status == "issued" else 0
    payment_state = "paid" if outstanding == 0 else ("partial" if outstanding < result.total else "unpaid")
    if outstanding > 0 and result.due < str(date.today()):
        payment_state = "overdue"
    return {
        "id": result.id,
        "number": result.number,
        "kind": result.kind,
        "customer_id": result.customer_id,
        "customer": result.snapshot.get("customer", ""),
        "author_id": result.author_id,
        "status": result.status,
        "revision": result.revision,
        "date": result.date,
        "due": result.due,
        "items": result.items,
        "subtotal": money(result.subtotal, result.decimals),
        "discount_rate": result.discount_rate,
        "discount": money(result.discount, result.decimals),
        "tax_rate": result.tax_rate,
        "tax": money(result.tax, result.decimals),
        "total": money(result.total, result.decimals),
        "currency": result.currency,
        "decimals": result.decimals,
        "terms": result.terms,
        "reason": result.reason,
        "quote_id": result.quote_id,
        "invoice_id": result.invoice_id,
        "outstanding": money(outstanding, result.decimals),
        "payment_state": payment_state,
    }
