"""add portfolio_snapshots table

Revision ID: b2d4f6a8c0e1
Revises: a1c3d5e7f9b2
Create Date: 2026-04-14 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2d4f6a8c0e1"
down_revision: Union[str, None] = "a1c3d5e7f9b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False, unique=True, index=True),
        sa.Column("total_exposure", sa.Float(), default=0),
        sa.Column("total_max_loss", sa.Float(), default=0),
        sa.Column("open_positions", sa.Integer(), default=0),
        sa.Column("beta_to_spy", sa.Float()),
        sa.Column("created_at", sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table("portfolio_snapshots")
