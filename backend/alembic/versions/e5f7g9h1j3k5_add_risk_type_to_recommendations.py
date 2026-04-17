"""Add risk_type column to recommendations

Revision ID: e5f7g9h1j3k5
Revises: d4f6h8j0l2n4
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa

revision = "e5f7g9h1j3k5"
down_revision = "d4f6h8j0l2n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recommendations", sa.Column("risk_type", sa.String(10), server_default="undefined"))


def downgrade() -> None:
    op.drop_column("recommendations", "risk_type")
