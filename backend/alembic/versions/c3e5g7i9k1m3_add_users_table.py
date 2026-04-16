"""add users table

Revision ID: c3e5g7i9k1m3
Revises: b2d4f6a8c0e1
Create Date: 2026-04-14 00:00:02.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3e5g7i9k1m3"
down_revision: Union[str, None] = "b2d4f6a8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Integer(), default=1),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("last_login", sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table("users")
