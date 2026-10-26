import os
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Base, SessionLocal, engine
from .migrate import prepare_single
from .models import (
    Allocation,
    Approval,
    Customer,
    Document,
    LedgerEntry,
    Opportunity,
    Payment,
    Product,
    Settings,
    Task,
    User,
)
from .schemas import DocumentInput, ItemInput
from .security import password_hash
from .services import audit, document_data, make_document, post_document


def issue(db: Session, document: Document, reviewer: User) -> None:
    document.status = "pending"
    db.add(
        Approval(
            document_id=document.id,
            revision=document.revision,
            requested_by=document.author_id,
            reviewer_id=reviewer.id,
            status="approved",
            comment="Fictional demo transaction",
            snapshot=document_data(db, document),
        )
    )
    post_document(db, document, reviewer)
    db.flush()


def seed() -> None:
    if os.environ.get("CRM_TENANCY", "single") == "hosted":
        raise SystemExit(
            "Demo data is only available in single-company mode; hosted workspaces never seed demo data"
        )
    if os.environ.get("CRM_ENV") == "production":
        raise RuntimeError("Demo data is available only in development")
    prepare_single(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.scalar(select(Customer.id).limit(1)):
            print("Demo seed skipped: this workspace already has customers.")
            return
        config = db.get(Settings, 1)
        if not config:
            config = Settings(id=1)
            db.add(config)
        config.company = "Nexera · Demo workspace"
        config.address = "120 Example Avenue, Business District"
        config.tax_id = "DEMO-TAX-1001"
        config.tax_rate = "5"
        config.is_demo = True
        members: dict[str, User] = {}
        for name, email, password, role in [
            ("Javed", "admin@crm.local", "CrmDemo!2026", "admin"),
            ("Maya Patel", "reviewer@crm.local", "ReviewDemo!2026", "manager"),
            ("Alex Morgan", "sales@crm.local", "SalesDemo!2026", "sales"),
            ("Sam Taylor", "finance@crm.local", "FinanceDemo!2026", "finance"),
        ]:
            member = db.scalar(select(User).where(User.email == email))
            if not member:
                member = User(name=name, email=email, password_hash=password_hash(password), role=role)
                db.add(member)
            members[role] = member
        db.flush()
        clients = []
        for index, (name, contact, tags) in enumerate(
            [
                ("Atlas Trading Co.", "Sarah Thompson", "Trading, VIP"),
                ("Northstar Technologies", "Daniel Reyes", "Technology"),
                ("Greenfield Hospitality", "Olivia Chen", "Hospitality, VIP"),
                ("Meridian Logistics", "James Wilson", "Logistics"),
                ("Oak & Stone Interiors", "Emma Brooks", "Design"),
                ("Brightside Consulting", "Noah Williams", "Services"),
                ("Evergreen Retail Group", "Sophia Ahmed", "Retail"),
                ("Summit Healthcare", "Liam Anderson", "Healthcare"),
            ]
        ):
            client = Customer(
                name=name,
                contact=contact,
                email=f"contact{index + 1}@example.test",
                phone=f"+1 202 555 01{index:02d}",
                address=f"{10 + index} Example Street",
                tags=tags,
                notes="Fictional sample customer. Replace with your own records.",
                contacts=[{"name": contact, "email": f"person{index}@example.test", "phone": ""}],
                addresses=[{"label": "Delivery", "address": f"{50 + index} Sample Road"}],
            )
            db.add(client)
            clients.append(client)
        db.flush()
        for name, description, unit, price in [
            ("Business consulting", "Business process consulting and advisory", "hour", 12500),
            ("CRM implementation", "CRM configuration and implementation package", "project", 550000),
            ("Managed support", "Monthly business support subscription", "month", 85000),
            ("Team training", "On-site team training workshop", "session", 180000),
            ("Equipment supply", "Office equipment supply and installation", "unit", 65000),
            ("Brand strategy", "Brand strategy and identity development", "project", 320000),
        ]:
            db.add(Product(name=name, description=description, unit=unit, price=price))
        now = date.today()
        amounts = [5850, 14800, 3150, 9700, 12600, 3400, 8400, 6500, 2800, 11500, 7200, 4100]
        for index, amount in enumerate(amounts):
            client = clients[index % len(clients)]
            issued_on = now - timedelta(days=150 - index * 12)
            document = make_document(
                db,
                DocumentInput(
                    kind="invoice",
                    customer_id=client.id,
                    date=issued_on,
                    due=issued_on + timedelta(days=30),
                    items=[
                        ItemInput(
                            description="Business services and implementation",
                            quantity=Decimal(1),
                            unit_price=Decimal(amount),
                        )
                    ],
                    tax_rate=Decimal(5),
                    terms="Payment due within 30 days.",
                ),
                members["finance"],
            )
            issue(db, document, members["manager"])
            paid = document.total if index < 6 else document.total // 2 if index < 10 else 0
            if paid:
                receipt_date = min(now, issued_on + timedelta(days=18))
                receipt = Payment(
                    customer_id=client.id,
                    amount=paid,
                    date=str(receipt_date),
                    reference=f"DEMO-RECEIPT-{index + 1:04d}",
                    method="Bank transfer",
                    idempotency_key=str(uuid4()),
                    request_fingerprint="demo",
                    created_by=members["finance"].id,
                )
                db.add(receipt)
                db.flush()
                db.add(Allocation(payment_id=receipt.id, invoice_id=document.id, amount=paid))
                db.add(
                    LedgerEntry(
                        customer_id=client.id,
                        payment_id=receipt.id,
                        date=receipt.date,
                        description=f"Payment · {receipt.reference}",
                        debit=0,
                        credit=paid,
                    )
                )
                audit(db, members["finance"], "Payment received", client.id, receipt.reference)
        for index, (title, stage, value, source) in enumerate(
            [
                ("Annual support agreement", "New", 10200, "Referral"),
                ("Retail rollout programme", "New", 18000, "Website"),
                ("Operations transformation", "Qualified", 24500, "Referral"),
                ("Workspace refresh", "Qualified", 12800, "Email"),
                ("Technology implementation", "Proposal", 16500, "Website"),
                ("Business advisory retainer", "Proposal", 9600, "Repeat business"),
                ("Regional expansion project", "Negotiation", 32000, "Referral"),
                ("Service renewal", "Negotiation", 8400, "Repeat business"),
                ("Team enablement programme", "Won", 7200, "Event"),
                ("Supply contract", "Lost", 5100, "Website"),
            ]
        ):
            db.add(
                Opportunity(
                    title=title,
                    customer_id=clients[index % 8].id,
                    owner_id=members["admin" if index % 2 == 0 else "sales"].id,
                    source=source,
                    stage=stage,
                    value=value * 100,
                    closing_date=str(now + timedelta(days=10 + index)),
                )
            )
        for index, (title, days) in enumerate(
            [
                ("Follow up on support proposal", -2),
                ("Discuss the next project phase", 0),
                ("Confirm invoice payment date", 1),
                ("Send revised service proposal", 3),
                ("Schedule a discovery call", -1),
                ("Review the annual contract", 5),
            ]
        ):
            db.add(
                Task(
                    title=title,
                    customer_id=clients[index].id,
                    owner_id=members["admin" if index < 4 else "sales"].id,
                    due=str(now + timedelta(days=days)),
                    notes="Fictional demo follow-up.",
                )
            )
        for index, (status, amount, description) in enumerate(
            [
                ("pending", 12400, "Annual business support programme"),
                ("sent", 8750, "Consulting and advisory engagement"),
                ("accepted", 5600, "Workspace setup and training"),
                ("draft", 18300, "Regional technology rollout"),
                ("pending", 3200, "Team development workshop"),
                ("rejected", 9800, "Process improvement programme"),
            ]
        ):
            document = make_document(
                db,
                DocumentInput(
                    kind="quote",
                    customer_id=clients[index].id,
                    date=now - timedelta(days=6 - index),
                    due=now + timedelta(days=24),
                    items=[
                        ItemInput(description=description, quantity=Decimal(1), unit_price=Decimal(amount))
                    ],
                    tax_rate=Decimal(5),
                    terms="Quotation valid for 30 days. Scope to be confirmed in writing.",
                ),
                members["admin"],
            )
            document.status = status
            if status != "draft":
                db.add(
                    Approval(
                        document_id=document.id,
                        revision=document.revision,
                        requested_by=members["admin"].id,
                        reviewer_id=None if status == "pending" else members["manager"].id,
                        status="pending"
                        if status == "pending"
                        else "rejected"
                        if status == "rejected"
                        else "approved",
                        comment="Please review the service scope." if status == "rejected" else "",
                        snapshot=document_data(db, document),
                    )
                )
            audit(db, members["admin"], f"{document.number}: {status}", document.customer_id, description)
        audit(
            db,
            members["admin"],
            "Demo workspace prepared",
            detail="All sample companies and transactions are fictional.",
        )
        db.commit()
    print("Demo workspace seeded. Development account credentials are listed in README.md.")


if __name__ == "__main__":
    seed()
