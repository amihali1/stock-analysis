"""Add short_interest_snapshots table (P10-003)

Revision ID: j0k2l4m6n8o0
Revises: i9j1k3l5m7n9
Create Date: 2026-05-04
"""
from alembic import op
import sqlalchemy as sa

revision = "j0k2l4m6n8o0"
down_revision = "i9j1k3l5m7n9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "short_interest_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("report_date", sa.Date, nullable=False),
        sa.Column("shares_short", sa.Float),
        sa.Column("short_percent_of_float", sa.Float),
        sa.Column("short_ratio_days_to_cover", sa.Float),
        sa.Column("has_data", sa.Integer, server_default="1"),
        sa.Column("fetched_at", sa.DateTime),
    )
    op.create_index(
        "ix_short_interest_ticker_report",
        "short_interest_snapshots",
        ["ticker", "report_date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_short_interest_ticker_report", table_name="short_interest_snapshots")
    op.drop_table("short_interest_snapshots")
