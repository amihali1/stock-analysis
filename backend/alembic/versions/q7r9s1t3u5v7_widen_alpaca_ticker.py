"""Widen ticker column on alpaca_positions and alpaca_orders for OCC option symbols

Revision ID: q7r9s1t3u5v7
Revises: p6q8r0s2t4u6
Create Date: 2026-05-26

portfolio_sync was crashing every 5 min with
psycopg2.errors.StringDataRightTruncation: value too long for type
character varying(10). Cause: alpaca_positions.ticker is String(10) but
OCC option symbols (e.g. INTC260626C00122000 = 18 chars) overflow.
Same bug latent in alpaca_orders.ticker. Widen both to 25.
"""
from alembic import op
import sqlalchemy as sa

revision = "q7r9s1t3u5v7"
down_revision = "p6q8r0s2t4u6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("alpaca_positions") as batch_op:
        batch_op.alter_column(
            "ticker",
            existing_type=sa.String(length=10),
            type_=sa.String(length=25),
            existing_nullable=False,
        )
    with op.batch_alter_table("alpaca_orders") as batch_op:
        batch_op.alter_column(
            "ticker",
            existing_type=sa.String(length=10),
            type_=sa.String(length=25),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("alpaca_orders") as batch_op:
        batch_op.alter_column(
            "ticker",
            existing_type=sa.String(length=25),
            type_=sa.String(length=10),
            existing_nullable=False,
        )
    with op.batch_alter_table("alpaca_positions") as batch_op:
        batch_op.alter_column(
            "ticker",
            existing_type=sa.String(length=25),
            type_=sa.String(length=10),
            existing_nullable=False,
        )
