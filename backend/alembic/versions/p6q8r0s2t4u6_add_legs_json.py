"""Add legs_json to recommendations and paper_trades for multi-leg spreads

Revision ID: p6q8r0s2t4u6
Revises: o5p7q9r1s3t5
Create Date: 2026-05-21

Multi-leg spread OCC routing needs per-leg strike/option_type/action stored so
execution_engine can build per-leg OCC symbols and submit a multi-leg order to
Alpaca. The single-strike columns (strike, option_type) stay for single-leg
options strategies; legs_json holds the full leg list for spread/bull_spread.
"""
from alembic import op
import sqlalchemy as sa

revision = "p6q8r0s2t4u6"
down_revision = "o5p7q9r1s3t5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.add_column(sa.Column("legs_json", sa.Text(), nullable=True))
    with op.batch_alter_table("paper_trades") as batch_op:
        batch_op.add_column(sa.Column("legs_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("paper_trades") as batch_op:
        batch_op.drop_column("legs_json")
    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.drop_column("legs_json")
