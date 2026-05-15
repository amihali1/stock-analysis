"""Add sec_filings_8k table (P10-009)

Revision ID: o5p7q9r1s3t5
Revises: n4o6p8q0r2s4
Create Date: 2026-05-15

Stores SEC Form 8-K filing metadata for catalyst-event features on the
drop-side model. Sentiment historical backfill via news APIs is blocked
(Finviz/Yahoo RSS only return recent items), so we lean on SEC EDGAR
which has structured event history going back to 1994. See memo
`sentiment_historical_backfill_pivot_2026-05-15`.
"""
from alembic import op
import sqlalchemy as sa

revision = "o5p7q9r1s3t5"
down_revision = "n4o6p8q0r2s4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sec_filings_8k",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("cik", sa.String(10), nullable=False),
        sa.Column("accession_number", sa.String(30), nullable=False),
        sa.Column("filing_date", sa.Date, nullable=False),
        sa.Column("items", sa.String(200), nullable=False),
        sa.Column("is_material", sa.Boolean, server_default=sa.false()),
        sa.Column("fetched_at", sa.DateTime),
    )
    op.create_index(
        "ix_sec_8k_accession", "sec_filings_8k", ["accession_number"], unique=True,
    )
    op.create_index(
        "ix_sec_8k_ticker_date", "sec_filings_8k", ["ticker", "filing_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_sec_8k_ticker_date", table_name="sec_filings_8k")
    op.drop_index("ix_sec_8k_accession", table_name="sec_filings_8k")
    op.drop_table("sec_filings_8k")
