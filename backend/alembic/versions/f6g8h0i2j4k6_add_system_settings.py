"""Add system_settings key/value table

Revision ID: f6g8h0i2j4k6
Revises: e5f7g9h1j3k5
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa

revision = "f6g8h0i2j4k6"
down_revision = "e5f7g9h1j3k5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(50), primary_key=True),
        sa.Column("value", sa.String(500), nullable=False),
        sa.Column("updated_at", sa.DateTime),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
