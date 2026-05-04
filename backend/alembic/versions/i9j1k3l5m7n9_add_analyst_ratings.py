"""Add analyst_ratings table (P10-001)

Revision ID: i9j1k3l5m7n9
Revises: h8i0j2k4l6m8
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa

revision = "i9j1k3l5m7n9"
down_revision = "h8i0j2k4l6m8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analyst_ratings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("firm", sa.String(100)),
        sa.Column("from_grade", sa.String(50)),
        sa.Column("to_grade", sa.String(50)),
        sa.Column("action", sa.String(20)),  # up, down, init, main, reit
        sa.Column("source", sa.String(20), server_default="yfinance"),
        sa.Column("fetched_at", sa.DateTime),
    )
    op.create_index(
        "ix_analyst_ratings_ticker_date",
        "analyst_ratings",
        ["ticker", "date"],
    )
    op.create_index(
        "ix_analyst_ratings_unique",
        "analyst_ratings",
        ["ticker", "date", "firm", "to_grade"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_analyst_ratings_unique", table_name="analyst_ratings")
    op.drop_index("ix_analyst_ratings_ticker_date", table_name="analyst_ratings")
    op.drop_table("analyst_ratings")
