"""Add sentiment_history and earnings_calendar tables (P9-004, P9-005)

Revision ID: h8i0j2k4l6m8
Revises: g7h9i1j3k5l7
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa

revision = "h8i0j2k4l6m8"
down_revision = "g7h9i1j3k5l7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sentiment_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("sentiment_score", sa.Float),
        sa.Column("confidence", sa.Float),
        sa.Column("article_count", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index(
        "ix_sentiment_history_ticker_date",
        "sentiment_history",
        ["ticker", "date"],
        unique=True,
    )

    op.create_table(
        "earnings_calendar",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("earnings_date", sa.Date, nullable=False),
        sa.Column("source", sa.String(20), server_default="yfinance"),
        sa.Column("fetched_at", sa.DateTime),
    )
    op.create_index(
        "ix_earnings_calendar_ticker_date",
        "earnings_calendar",
        ["ticker", "earnings_date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_earnings_calendar_ticker_date", table_name="earnings_calendar")
    op.drop_table("earnings_calendar")
    op.drop_index("ix_sentiment_history_ticker_date", table_name="sentiment_history")
    op.drop_table("sentiment_history")
