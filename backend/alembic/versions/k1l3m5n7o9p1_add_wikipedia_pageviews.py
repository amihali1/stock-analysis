"""Add wikipedia_pageviews table (P10-008)

Revision ID: k1l3m5n7o9p1
Revises: j0k2l4m6n8o0
Create Date: 2026-05-07
"""
from alembic import op
import sqlalchemy as sa

revision = "k1l3m5n7o9p1"
down_revision = "j0k2l4m6n8o0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wikipedia_pageviews",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("view_date", sa.Date, nullable=False),
        sa.Column("page_views", sa.Integer, server_default="0"),
        sa.Column("wikipedia_title", sa.String(200)),
        sa.Column("fetched_at", sa.DateTime),
    )
    op.create_index(
        "ix_wikipedia_pageviews_ticker_date",
        "wikipedia_pageviews",
        ["ticker", "view_date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_wikipedia_pageviews_ticker_date", table_name="wikipedia_pageviews")
    op.drop_table("wikipedia_pageviews")
