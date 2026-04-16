"""add trading_log, alpaca_positions, alpaca_orders tables

Revision ID: d4f6h8j0l2n4
Revises: c3e5g7i9k1m3
Create Date: 2026-04-14 00:00:03.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4f6h8j0l2n4"
down_revision: Union[str, None] = "c3e5g7i9k1m3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trading_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("strategy", sa.String(20)),
        sa.Column("qty", sa.Float()),
        sa.Column("side", sa.String(10)),
        sa.Column("order_id", sa.String(100)),
        sa.Column("reason", sa.Text()),
        sa.Column("passed_safety", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "alpaca_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False, index=True),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("side", sa.String(10), server_default="long"),
        sa.Column("avg_entry_price", sa.Float()),
        sa.Column("current_price", sa.Float()),
        sa.Column("market_value", sa.Float()),
        sa.Column("unrealized_pl", sa.Float()),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "alpaca_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alpaca_order_id", sa.String(100), unique=True, index=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("side", sa.String(10)),
        sa.Column("qty", sa.Float()),
        sa.Column("order_type", sa.String(20)),
        sa.Column("status", sa.String(20)),
        sa.Column("filled_price", sa.Float()),
        sa.Column("submitted_at", sa.DateTime()),
        sa.Column("filled_at", sa.DateTime()),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("alpaca_orders")
    op.drop_table("alpaca_positions")
    op.drop_table("trading_log")
