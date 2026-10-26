"""Accounting outbox, accounting state and invites (additive)

Revision ID: 0002_accounting_outbox
Revises: 0001_baseline
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_accounting_outbox"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "crm_accounting_state" not in existing:
        op.create_table(
            "crm_accounting_state",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("binding_id", sa.String(length=36), nullable=True),
            sa.Column("erp_tenant_id", sa.String(length=36), nullable=True),
            sa.Column("erp_company_id", sa.String(length=36), nullable=True),
            sa.Column("erp_company_name", sa.String(length=200), nullable=False),
            sa.Column("erp_tenant_name", sa.String(length=200), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("cutoff_ledger_id", sa.Integer(), nullable=False),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("paused_reason", sa.String(length=60), nullable=True),
            sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if "crm_invites" not in existing:
        op.create_table(
            "crm_invites",
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("expires", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["crm_users.id"],
            ),
            sa.PrimaryKeyConstraint("token_hash"),
        )
    if "crm_accounting_outbox" not in existing:
        op.create_table(
            "crm_accounting_outbox",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("event_id", sa.String(length=100), nullable=False),
            sa.Column("event_type", sa.String(length=40), nullable=False),
            sa.Column("source_type", sa.String(length=20), nullable=False),
            sa.Column("source_id", sa.Integer(), nullable=False),
            sa.Column("ledger_id", sa.Integer(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("payload_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("lease_owner", sa.String(length=120), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_http_status", sa.Integer(), nullable=True),
            sa.Column("last_error_code", sa.String(length=60), nullable=True),
            sa.Column("last_error_message", sa.String(length=500), nullable=True),
            sa.Column("erp_journal_id", sa.String(length=36), nullable=True),
            sa.Column("erp_journal_number", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "event_type IN ('invoice.issued','payment.received','credit_note.issued','refund.paid','opening.recorded','adjustment.recorded')",
                name="ck_outbox_event_type",
            ),
            sa.CheckConstraint("source_type IN ('document','payment')", name="ck_outbox_source_type"),
            sa.CheckConstraint(
                "status IN ('pending','in_progress','posted','retrying','blocked','held','conflict','skipped')",
                name="ck_outbox_status",
            ),
            sa.ForeignKeyConstraint(
                ["ledger_id"],
                ["crm_ledger.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id"),
            sa.UniqueConstraint("ledger_id"),
            sa.UniqueConstraint("source_type", "source_id", "event_type", name="uq_outbox_source_event"),
        )
        op.create_index(
            "ix_outbox_status_next_attempt", "crm_accounting_outbox", ["status", "next_attempt_at"], unique=False
        )


def downgrade() -> None:
    raise NotImplementedError("Downgrades are not supported; migrations are additive")
