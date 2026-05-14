"""Widen strategy column on recommendations and paper_trades

Revision ID: n4o6p8q0r2s4
Revises: m3n5o7p9q1r3
Create Date: 2026-05-14

Phase 5 (PR #36) added 'call_options' (12) and 'bull_spread' (11) as strategy
values, but the column was still String(10). First production scheduler run
on 2026-05-14 07:30 EDT failed with psycopg2.errors.StringDataRightTruncation
and rolled back the entire batch insert. Widening to 32 leaves headroom for
future strategy labels.
"""
from alembic import op
import sqlalchemy as sa

revision = "n4o6p8q0r2s4"
down_revision = "m3n5o7p9q1r3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.alter_column(
            "strategy",
            existing_type=sa.String(length=10),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
    with op.batch_alter_table("paper_trades") as batch_op:
        batch_op.alter_column(
            "strategy",
            existing_type=sa.String(length=10),
            type_=sa.String(length=32),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("paper_trades") as batch_op:
        batch_op.alter_column(
            "strategy",
            existing_type=sa.String(length=32),
            type_=sa.String(length=10),
            existing_nullable=False,
        )
    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.alter_column(
            "strategy",
            existing_type=sa.String(length=32),
            type_=sa.String(length=10),
            existing_nullable=False,
        )
