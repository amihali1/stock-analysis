"""add options_chain table

Revision ID: a1c3d5e7f9b2
Revises: 6b645aae8a3b
Create Date: 2026-04-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1c3d5e7f9b2"
down_revision: Union[str, None] = "6b645aae8a3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "options_chain",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False, index=True),
        sa.Column("expiration", sa.String(10), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("option_type", sa.String(4), nullable=False),
        sa.Column("bid", sa.Float(), default=0),
        sa.Column("ask", sa.Float(), default=0),
        sa.Column("last", sa.Float(), default=0),
        sa.Column("volume", sa.Integer(), default=0),
        sa.Column("open_interest", sa.Integer(), default=0),
        sa.Column("implied_vol", sa.Float(), default=0),
        sa.Column("fetched_at", sa.DateTime()),
    )
    op.create_index(
        "ix_options_chain_ticker_exp", "options_chain", ["ticker", "expiration"]
    )


def downgrade() -> None:
    op.drop_index("ix_options_chain_ticker_exp", table_name="options_chain")
    op.drop_table("options_chain")
