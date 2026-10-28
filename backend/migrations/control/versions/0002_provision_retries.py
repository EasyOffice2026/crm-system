"""Durable onboarding retries.

Revision ID: 0002_provision_retries
Revises: 0001_control
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_provision_retries"
down_revision = "0001_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_workspaces", sa.Column("provision_attempts", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "crm_workspaces", sa.Column("next_provision_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    raise NotImplementedError("Downgrades are not supported; migrations are additive")
