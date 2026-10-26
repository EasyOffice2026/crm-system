import re
from datetime import date as Date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class LoginInput(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class UserInput(Input):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)
    role: Literal["admin", "manager", "sales", "finance"]


class Contact(Input):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=80)


class Address(Input):
    label: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=4000)


class CustomerInput(Input):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["company", "individual"] = "company"
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=80)
    contact: str = Field(default="", max_length=200)
    address: str = Field(default="", max_length=4000)
    tax_id: str = Field(default="", max_length=100)
    tags: str = Field(default="", max_length=300)
    payment_days: int = Field(default=30, ge=0, le=365)
    contacts: list[Contact] = Field(default_factory=list, max_length=30)
    addresses: list[Address] = Field(default_factory=list, max_length=30)
    notes: str = Field(default="", max_length=10000)


class ProductInput(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    unit: str = Field(default="item", min_length=1, max_length=30)
    price: Decimal = Field(ge=0, le=1000000000)
    active: bool = True


class OpportunityInput(Input):
    title: str = Field(min_length=1, max_length=200)
    customer_id: int | None = None
    owner_id: int
    source: str = Field(default="Website", max_length=80)
    stage: Literal["New", "Qualified", "Proposal", "Negotiation", "Won", "Lost"] = "New"
    value: Decimal = Field(default=Decimal(0), ge=0, le=1000000000)
    closing_date: Date | None = None
    notes: str = Field(default="", max_length=10000)


class TaskInput(Input):
    title: str = Field(min_length=1, max_length=200)
    customer_id: int | None = None
    owner_id: int
    due: Date
    notes: str = Field(default="", max_length=4000)


class ItemInput(Input):
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(gt=0, le=1000000, decimal_places=4)
    unit_price: Decimal = Field(ge=0, le=1000000000)


class DocumentInput(Input):
    kind: Literal["quote", "invoice", "credit", "opening", "adjustment", "refund"]
    customer_id: int
    invoice_id: int | None = None
    date: Date
    due: Date
    items: list[ItemInput] = Field(default_factory=list, max_length=100)
    discount_rate: Decimal = Field(default=Decimal(0), ge=0, le=100)
    tax_rate: Decimal = Field(default=Decimal(0), ge=0, le=100)
    amount: Decimal = Field(default=Decimal(0), ge=-1000000000, le=1000000000)
    terms: str = Field(default="", max_length=4000)
    reason: str = Field(default="", max_length=4000)


class ActionInput(Input):
    action: Literal["submit", "send", "accept", "decline", "convert", "cancel"]


class ErpActivationInput(Input):
    tenant_id: str = Field(min_length=36, max_length=36)
    company_id: str = Field(min_length=36, max_length=36)
    reconciled: Literal[True]
    cutoff_ledger_id: int = Field(ge=0)
    account_ids: dict[
        Literal["receivable", "revenue", "receipts", "returns", "opening", "adjustments", "tax"], str
    ] = Field(max_length=7)


class DecisionInput(Input):
    decision: Literal["approved", "rejected"]
    comment: str = Field(default="", max_length=2000)


class AllocationInput(Input):
    invoice_id: int
    amount: Decimal = Field(gt=0, le=1000000000)


class PaymentInput(Input):
    customer_id: int
    amount: Decimal = Field(gt=0, le=1000000000)
    date: Date
    reference: str = Field(min_length=1, max_length=150)
    method: Literal["Bank transfer", "Cash", "Card", "Cheque", "Other"] = "Bank transfer"
    idempotency_key: str = Field(min_length=16, max_length=100)
    allocations: list[AllocationInput] = Field(default_factory=list, max_length=100)


class SettingsInput(Input):
    company: str = Field(min_length=1, max_length=200)
    address: str = Field(default="", max_length=4000)
    tax_id: str = Field(default="", max_length=100)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    decimals: Literal[0, 2, 3] = 2
    tax_rate: Decimal = Field(ge=0, le=100)
    payment_days: int = Field(ge=0, le=365)
    terms: str = Field(default="", max_length=4000)
    approval_threshold: Decimal = Field(ge=0, le=1000000000)
    discount_threshold: Decimal = Field(ge=0, le=100)
    number_prefixes: dict[str, str] = Field(
        default_factory=lambda: {
            "quote": "QUO",
            "invoice": "INV",
            "credit": "CRN",
            "opening": "OPN",
            "adjustment": "ADJ",
            "refund": "REF",
        }
    )

    @field_validator("number_prefixes")
    @classmethod
    def valid_prefixes(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != {"quote", "invoice", "credit", "opening", "adjustment", "refund"}:
            raise ValueError("Set a prefix for each document type")
        if not all(re.fullmatch(r"[A-Z]{2,8}", prefix) for prefix in value.values()):
            raise ValueError("Document prefixes must contain 2–8 uppercase letters")
        return value

    @field_validator("currency")
    @classmethod
    def supported_currency(cls, value: str) -> str:
        if value not in {"USD", "EUR", "GBP", "KWD", "AED", "SAR", "QAR", "BHD", "OMR", "INR"}:
            raise ValueError("Choose a supported currency")
        return value
