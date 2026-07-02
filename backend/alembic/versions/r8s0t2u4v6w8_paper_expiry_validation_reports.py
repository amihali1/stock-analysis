"""Add paper_trades.expiry and the validation_reports table

Revision ID: r8s0t2u4v6w8
Revises: q7r9s1t3u5v7
Create Date: 2026-07-02

paper_trades had no expiry, so option/spread positions could never be
closed automatically — 4 trades from 2026-06-09 sat open past any
plausible expiry. The new exit-evaluation job needs the real expiry
persisted at submission time (mirrored from the recommendation).

validation_reports stores the weekly paper-vs-backtest scoreboard so
live-readiness is a queryable time series instead of an ad-hoc script
run.
"""
from alembic import op
import sqlalchemy as sa

revision = "r8s0t2u4v6w8"
down_revision = "q7r9s1t3u5v7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_trades", sa.Column("expiry", sa.Date(), nullable=True))
    op.create_table(
        "validation_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("num_paper_trades", sa.Integer(), nullable=False),
        sa.Column("paper_win_rate", sa.Float(), nullable=False),
        sa.Column("paper_total_pnl", sa.Float(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("validation_reports")
    op.drop_column("paper_trades", "expiry")
