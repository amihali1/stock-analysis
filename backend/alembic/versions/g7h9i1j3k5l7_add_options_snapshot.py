"""Add options_snapshots table for daily IV features

Revision ID: g7h9i1j3k5l7
Revises: f6g8h0i2j4k6
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa

revision = "g7h9i1j3k5l7"
down_revision = "f6g8h0i2j4k6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "options_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("iv_atm_30d", sa.Float),
        sa.Column("iv_atm_90d", sa.Float),
        sa.Column("iv_rank_252d", sa.Float),
        sa.Column("iv_percentile_252d", sa.Float),
        sa.Column("put_call_skew_25d", sa.Float),
        sa.Column("term_structure_slope", sa.Float),
        sa.Column("has_options", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index(
        "ix_options_snapshot_ticker_date",
        "options_snapshots",
        ["ticker", "date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_options_snapshot_ticker_date", table_name="options_snapshots")
    op.drop_table("options_snapshots")
