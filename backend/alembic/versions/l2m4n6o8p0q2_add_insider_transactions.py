"""Add insider_transactions and sec_cik_map tables (P10-005)

Revision ID: l2m4n6o8p0q2
Revises: k1l3m5n7o9p1
Create Date: 2026-05-08
"""
from alembic import op
import sqlalchemy as sa

revision = "l2m4n6o8p0q2"
down_revision = "k1l3m5n7o9p1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sec_cik_map",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("cik", sa.String(10), nullable=False),
        sa.Column("company_name", sa.String(200)),
        sa.Column("fetched_at", sa.DateTime),
    )
    op.create_index("ix_sec_cik_map_ticker", "sec_cik_map", ["ticker"], unique=True)

    op.create_table(
        "insider_transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("accession_number", sa.String(30), nullable=False),
        sa.Column("filing_date", sa.Date, nullable=False),
        sa.Column("transaction_date", sa.Date, nullable=False),
        sa.Column("insider_name", sa.String(200)),
        sa.Column("insider_title", sa.String(200)),
        sa.Column("transaction_code", sa.String(2)),
        sa.Column("shares", sa.Float),
        sa.Column("price_per_share", sa.Float),
        sa.Column("total_value", sa.Float),
        sa.Column("shares_owned_after", sa.Float),
        sa.Column("is_director", sa.Boolean, server_default=sa.false()),
        sa.Column("is_officer", sa.Boolean, server_default=sa.false()),
        sa.Column("is_10pct_owner", sa.Boolean, server_default=sa.false()),
        sa.Column("fetched_at", sa.DateTime),
    )
    op.create_index(
        "ix_insider_tx_accession",
        "insider_transactions",
        ["accession_number"],
        unique=True,
    )
    op.create_index(
        "ix_insider_tx_ticker_date",
        "insider_transactions",
        ["ticker", "transaction_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_insider_tx_ticker_date", table_name="insider_transactions")
    op.drop_index("ix_insider_tx_accession", table_name="insider_transactions")
    op.drop_table("insider_transactions")
    op.drop_index("ix_sec_cik_map_ticker", table_name="sec_cik_map")
    op.drop_table("sec_cik_map")
