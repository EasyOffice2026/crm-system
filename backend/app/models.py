from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "crm_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(Base):
    __tablename__ = "crm_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("crm_users.id"))
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Settings(Base):
    __tablename__ = "crm_settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    company: Mapped[str] = mapped_column(String(200), default="Your Business")
    address: Mapped[str] = mapped_column(Text, default="")
    tax_id: Mapped[str] = mapped_column(String(100), default="")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    decimals: Mapped[int] = mapped_column(Integer, default=2)
    tax_rate: Mapped[str] = mapped_column(String(20), default="0")
    payment_days: Mapped[int] = mapped_column(Integer, default=30)
    terms: Mapped[str] = mapped_column(Text, default="Thank you for your business.")
    approval_threshold: Mapped[int] = mapped_column(Integer, default=0)
    discount_threshold: Mapped[str] = mapped_column(String(20), default="0")
    number_prefixes: Mapped[dict[str, str]] = mapped_column(
        JSON,
        default=lambda: {
            "quote": "QUO",
            "invoice": "INV",
            "credit": "CRN",
            "opening": "OPN",
            "adjustment": "ADJ",
            "refund": "REF",
        },
    )
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Customer(Base):
    __tablename__ = "crm_customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="company")
    email: Mapped[str] = mapped_column(String(254), default="")
    phone: Mapped[str] = mapped_column(String(80), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    tax_id: Mapped[str] = mapped_column(String(100), default="")
    contact: Mapped[str] = mapped_column(String(200), default="")
    contacts: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    addresses: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    tags: Mapped[str] = mapped_column(String(300), default="")
    payment_days: Mapped[int] = mapped_column(Integer, default=30)
    notes: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Product(Base):
    __tablename__ = "crm_products"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str] = mapped_column(String(30), default="item")
    price: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Opportunity(Base):
    __tablename__ = "crm_opportunities"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("crm_customers.id"))
    owner_id: Mapped[int] = mapped_column(ForeignKey("crm_users.id"))
    source: Mapped[str] = mapped_column(String(80), default="Website")
    stage: Mapped[str] = mapped_column(String(30), default="New")
    value: Mapped[int] = mapped_column(Integer, default=0)
    closing_date: Mapped[str] = mapped_column(String(10), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class Task(Base):
    __tablename__ = "crm_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("crm_customers.id"))
    owner_id: Mapped[int] = mapped_column(ForeignKey("crm_users.id"))
    due: Mapped[str] = mapped_column(String(10))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")


class Document(Base):
    __tablename__ = "crm_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    customer_id: Mapped[int] = mapped_column(ForeignKey("crm_customers.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("crm_users.id"))
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("crm_documents.id"), unique=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("crm_documents.id"))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    date: Mapped[str] = mapped_column(String(10))
    due: Mapped[str] = mapped_column(String(10))
    items: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    subtotal: Mapped[int] = mapped_column(Integer, default=0)
    discount_rate: Mapped[str] = mapped_column(String(20), default="0")
    discount: Mapped[int] = mapped_column(Integer, default=0)
    tax_rate: Mapped[str] = mapped_column(String(20), default="0")
    tax: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    decimals: Mapped[int] = mapped_column(Integer)
    terms: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DocumentRevision(Base):
    __tablename__ = "crm_document_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("crm_documents.id"))
    revision: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, object]] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Approval(Base):
    __tablename__ = "crm_approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("crm_documents.id"))
    revision: Mapped[int] = mapped_column(Integer)
    requested_by: Mapped[int] = mapped_column(ForeignKey("crm_users.id"))
    snapshot: Mapped[dict[str, object]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("crm_users.id"))
    comment: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Payment(Base):
    __tablename__ = "crm_payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("crm_customers.id"))
    amount: Mapped[int] = mapped_column(Integer)
    date: Mapped[str] = mapped_column(String(10))
    reference: Mapped[str] = mapped_column(String(150))
    method: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(ForeignKey("crm_users.id"))


class Allocation(Base):
    __tablename__ = "crm_allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("crm_payments.id"))
    invoice_id: Mapped[int] = mapped_column(ForeignKey("crm_documents.id"))
    amount: Mapped[int] = mapped_column(Integer)


class LedgerEntry(Base):
    __tablename__ = "crm_ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("crm_customers.id"))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("crm_documents.id"), unique=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("crm_payments.id"), unique=True)
    date: Mapped[str] = mapped_column(String(10))
    description: Mapped[str] = mapped_column(Text)
    debit: Mapped[int] = mapped_column(Integer, default=0)
    credit: Mapped[int] = mapped_column(Integer, default=0)


class Audit(Base):
    __tablename__ = "crm_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("crm_users.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("crm_customers.id"))
    action: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Attachment(Base):
    __tablename__ = "crm_attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("crm_customers.id"))
    name: Mapped[str] = mapped_column(String(250))
    storage_key: Mapped[str] = mapped_column(String(80), unique=True)
    size: Mapped[int] = mapped_column(Integer)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
