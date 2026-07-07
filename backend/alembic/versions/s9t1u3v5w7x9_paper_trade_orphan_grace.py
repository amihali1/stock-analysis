"""Add paper_trades.orphan_seen_at for orphan-close grace period

Revision ID: s9t1u3v5w7x9
Revises: r8s0t2u4v6w8
Create Date: 2026-07-07

The portfolio-sync orphan sweep closed PaperTrades on a single sync
cycle's evidence. Two incidents on 2026-07-06/07:

- Submit-to-fill race: MU and LRCX closed 5 minutes after submission
  because the orphan check ran before sync_orders had ever seen their
  in-flight orders.
- Transient API hiccup: one get_positions call returned 1 of 6
  positions; all 4 live option trades were mass-closed with NULL pnl.

orphan_seen_at records when a trade was FIRST observed orphaned; the
sweep only closes after the condition persists past a grace window,
and clears the marker whenever the position/order reappears.
"""
from alembic import op
import sqlalchemy as sa

revision = "s9t1u3v5w7x9"
down_revision = "r8s0t2u4v6w8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_trades", sa.Column("orphan_seen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("paper_trades", "orphan_seen_at")
