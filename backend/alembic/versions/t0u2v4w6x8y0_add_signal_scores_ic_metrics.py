"""Add signal_scores + ic_metrics tables for IC-based scoring

Revision ID: t0u2v4w6x8y0
Revises: s9t1u3v5w7x9
Create Date: 2026-09-08

IC-rebuild Phase 0. `signal_scores` persists the FULL scored universe each
recommendations run (not just the funded top-K in `recommendations`) so the
cross-sectional Information Coefficient of each signal can be measured against
forward returns. `ic_metrics` is the resulting daily IC time series — the
go/no-go metric that replaces win-rate. See ensemble.py / rec_ranker.py and the
2026-09-08 audit (composite score IC was negative).
"""
from alembic import op
import sqlalchemy as sa

revision = "t0u2v4w6x8y0"
down_revision = "s9t1u3v5w7x9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signal_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("direction", sa.String(length=5), nullable=False),
        sa.Column("drop_prob", sa.Float(), nullable=True),
        sa.Column("rise_prob", sa.Float(), nullable=True),
        sa.Column("predicted_vol", sa.Float(), nullable=True),
        sa.Column("sentiment_score", sa.Float(), nullable=True),
        sa.Column("sentiment_confidence", sa.Float(), nullable=True),
        sa.Column("composite_score", sa.Float(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_signal_scores_date_ticker_dir",
        "signal_scores", ["date", "ticker", "direction"], unique=True,
    )
    op.create_index(
        "ix_signal_scores_date_dir", "signal_scores", ["date", "direction"],
    )

    op.create_table(
        "ic_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("signal", sa.String(length=20), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("sample", sa.String(length=12), nullable=False, server_default="universe"),
        sa.Column("ic", sa.Float(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_ic_metrics_date_signal",
        "ic_metrics", ["date", "signal", "horizon", "sample"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_ic_metrics_date_signal", table_name="ic_metrics")
    op.drop_table("ic_metrics")
    op.drop_index("ix_signal_scores_date_dir", table_name="signal_scores")
    op.drop_index("ix_signal_scores_date_ticker_dir", table_name="signal_scores")
    op.drop_table("signal_scores")
