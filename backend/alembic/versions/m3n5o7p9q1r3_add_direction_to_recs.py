"""Add direction column to recommendations and paper_trades

Revision ID: m3n5o7p9q1r3
Revises: l2m4n6o8p0q2
Create Date: 2026-05-12

Phase 0 of bullish-side build. Adds a 'direction' column ('long' | 'short') to
recommendations and paper_trades so the pipeline can surface both bull and bear
trades. Existing rows backfill to 'short' (the only direction we've ever
produced). After the bullish models/sizers ship, new rows will populate this
column explicitly.
"""
from alembic import op
import sqlalchemy as sa

revision = "m3n5o7p9q1r3"
down_revision = "l2m4n6o8p0q2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recommendations",
        sa.Column("direction", sa.String(5), nullable=False, server_default="short"),
    )
    op.add_column(
        "paper_trades",
        sa.Column("direction", sa.String(5), nullable=False, server_default="short"),
    )
    op.create_index("ix_rec_date_direction", "recommendations", ["date", "direction"])


def downgrade() -> None:
    op.drop_index("ix_rec_date_direction", table_name="recommendations")
    op.drop_column("paper_trades", "direction")
    op.drop_column("recommendations", "direction")
