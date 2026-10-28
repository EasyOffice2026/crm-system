"""Baseline: CRM schema as of the standalone CRM integration branch

Revision ID: 0001_baseline
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_customers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("phone", sa.String(length=80), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("tax_id", sa.String(length=100), nullable=False),
        sa.Column("contact", sa.String(length=200), nullable=False),
        sa.Column("contacts", sa.JSON(), nullable=False),
        sa.Column("addresses", sa.JSON(), nullable=False),
        sa.Column("tags", sa.String(length=300), nullable=False),
        sa.Column("payment_days", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crm_erp_link",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("cutoff_ledger_id", sa.Integer(), nullable=False),
        sa.Column("account_ids", sa.JSON(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crm_products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=30), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crm_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company", sa.String(length=200), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("tax_id", sa.String(length=100), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("decimals", sa.Integer(), nullable=False),
        sa.Column("tax_rate", sa.String(length=20), nullable=False),
        sa.Column("payment_days", sa.Integer(), nullable=False),
        sa.Column("terms", sa.Text(), nullable=False),
        sa.Column("approval_threshold", sa.Integer(), nullable=False),
        sa.Column("discount_threshold", sa.String(length=20), nullable=False),
        sa.Column("number_prefixes", sa.JSON(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crm_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "crm_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=250), nullable=False),
        sa.Column("storage_key", sa.String(length=80), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["crm_customers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_table(
        "crm_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["crm_users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["crm_customers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crm_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=True),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("due", sa.String(length=10), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("subtotal", sa.Integer(), nullable=False),
        sa.Column("discount_rate", sa.String(length=20), nullable=False),
        sa.Column("discount", sa.Integer(), nullable=False),
        sa.Column("tax_rate", sa.String(length=20), nullable=False),
        sa.Column("tax", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("decimals", sa.Integer(), nullable=False),
        sa.Column("terms", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["crm_users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["crm_customers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["crm_documents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["crm_documents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
        sa.UniqueConstraint("quote_id"),
    )
    op.create_table(
        "crm_opportunities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("closing_date", sa.String(length=10), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["crm_customers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["crm_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crm_payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("reference", sa.String(length=150), nullable=False),
        sa.Column("method", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["crm_users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["crm_customers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_table(
        "crm_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("expires", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["crm_users.id"],
        ),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_table(
        "crm_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("due", sa.String(length=10), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["crm_customers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["crm_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crm_allocations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["crm_documents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["crm_payments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crm_approvals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["crm_documents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["crm_users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["crm_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crm_document_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["crm_documents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crm_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("debit", sa.Integer(), nullable=False),
        sa.Column("credit", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["crm_customers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["crm_documents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["crm_payments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
        sa.UniqueConstraint("payment_id"),
    )
    op.create_table(
        "crm_erp_postings",
        sa.Column("ledger_id", sa.Integer(), nullable=False),
        sa.Column("journal_id", sa.String(length=36), nullable=False),
        sa.Column("journal_number", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ledger_id"],
            ["crm_ledger.id"],
        ),
        sa.PrimaryKeyConstraint("ledger_id"),
        sa.UniqueConstraint("journal_id"),
    )


def downgrade() -> None:
    raise NotImplementedError("Downgrades are not supported; migrations are additive")
