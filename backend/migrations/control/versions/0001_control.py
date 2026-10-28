"""Control plane: workspaces, credentials, provider audit

Revision ID: 0001_control
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_control"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_provider_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=True),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crm_workspaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=41), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provision_request_id", sa.String(length=36), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_body", sa.JSON(), nullable=False),
        sa.Column("provision_step", sa.String(length=40), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("db_name", sa.String(length=63), nullable=False),
        sa.Column("db_role", sa.String(length=63), nullable=False),
        sa.Column("db_password_enc", sa.Text(), nullable=False),
        sa.Column("upload_root", sa.Text(), nullable=False),
        sa.Column("erp_tenant_id", sa.String(length=36), nullable=True),
        sa.Column("erp_company_id", sa.String(length=36), nullable=True),
        sa.Column("erp_binding_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('provisioning','active','suspended','failed')", name="ck_workspace_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("db_name"),
        sa.UniqueConstraint("provision_request_id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "crm_workspace_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("token_enc", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','active','revoking','revoked')", name="ck_workspace_credential_status"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["crm_workspaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_workspace_active_credential",
        "crm_workspace_credentials",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    raise NotImplementedError("Downgrades are not supported; migrations are additive")
