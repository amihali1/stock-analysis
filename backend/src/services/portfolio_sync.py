"""Sync positions, orders, and account data from Alpaca into local DB."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from src.db.models import AlpacaOrder, AlpacaPosition, PaperTrade
from src.services.alpaca_client import AlpacaClient, _underlying_from_occ

logger = logging.getLogger(__name__)

# Alpaca order statuses that indicate the order is still working and the
# underlying PaperTrade should NOT be auto-closed by the sync sweep.
_ALPACA_OPEN_ORDER_STATUSES = frozenset({
    "new",
    "accepted",
    "accepted_for_bidding",
    "pending_new",
    "pending_replace",
    "partially_filled",
    "held",
})


def _to_underlying(ticker: str | None) -> str | None:
    """Map an OCC option symbol to its underlying; pass through plain tickers."""
    if not ticker:
        return None
    return _underlying_from_occ(ticker) or ticker


class PortfolioSync:
    """Pulls data from Alpaca and upserts into local tables."""

    def __init__(self, db: Session, client: AlpacaClient | None = None):
        self.db = db
        self.client = client or AlpacaClient()

    def sync_positions(self) -> int:
        """Pull all open positions from Alpaca and upsert into alpaca_positions.

        Returns count of synced positions. The orphan PaperTrade sweep is a
        separate step (`close_orphan_paper_trades`) that the scheduler job runs
        AFTER sync_orders — running it here closed just-submitted trades whose
        in-flight orders had never been synced (MU/LRCX, 2026-07-06/07).
        """
        positions = self.client.get_positions()
        now = datetime.utcnow()

        # Clear stale positions, then upsert current
        self.db.query(AlpacaPosition).delete()

        for p in positions:
            self.db.add(AlpacaPosition(
                ticker=p["ticker"],
                qty=p["qty"],
                side=p["side"],
                avg_entry_price=p["avg_entry_price"],
                current_price=p["current_price"],
                market_value=p["market_value"],
                unrealized_pl=p["unrealized_pl"],
                synced_at=now,
            ))

        self.db.commit()
        logger.info(f"Synced {len(positions)} Alpaca positions")
        return len(positions)

    # A trade must be continuously orphaned for this long before the sweep
    # closes it. Covers both submit-to-fill latency and transient Alpaca API
    # responses (2026-07-07: one get_positions call returned 1 of 6 positions
    # and all 4 live option trades were mass-closed with NULL pnl).
    ORPHAN_GRACE_MINUTES = 30

    def close_orphan_paper_trades(self, now: datetime | None = None) -> int:
        """Close PaperTrade(status='open') rows whose underlying has had no
        live position or in-flight order for ORPHAN_GRACE_MINUTES.

        First orphan sighting stamps `orphan_seen_at`; the trade only closes
        once the condition persists past the grace window. Any reappearance
        of the position/order clears the stamp. Underlying ticker comparison
        handles OCC option symbols by mapping them back to the underlying.
        This keeps the position-limit safety rail honest without letting a
        single flaky sync destroy live trades.
        """
        now = now or datetime.utcnow()
        active: set[str] = set()
        for pos in self.db.query(AlpacaPosition).all():
            u = _to_underlying(pos.ticker)
            if u:
                active.add(u)
        in_flight = (
            self.db.query(AlpacaOrder)
            .filter(AlpacaOrder.status.in_(_ALPACA_OPEN_ORDER_STATUSES))
            .all()
        )
        for order in in_flight:
            u = _to_underlying(order.ticker)
            if u:
                active.add(u)

        closed = 0
        grace = timedelta(minutes=self.ORPHAN_GRACE_MINUTES)
        for pt in self.db.query(PaperTrade).filter_by(status="open").all():
            if pt.ticker in active:
                if pt.orphan_seen_at is not None:
                    pt.orphan_seen_at = None
                continue
            if pt.orphan_seen_at is None:
                pt.orphan_seen_at = now
                logger.info(
                    "PaperTrade %s (%s) orphan candidate — closing after %d min "
                    "unless position/order reappears",
                    pt.id, pt.ticker, self.ORPHAN_GRACE_MINUTES,
                )
                continue
            if now - pt.orphan_seen_at >= grace:
                pt.status = "closed"
                pt.closed_at = now
                closed += 1
                logger.warning(
                    "Auto-closed orphan PaperTrade %s (%s %s) — no position/order "
                    "since %s. pnl left NULL; reconcile from broker fills if needed.",
                    pt.id, pt.ticker, pt.strategy, pt.orphan_seen_at.isoformat(),
                )
        self.db.commit()
        if closed:
            logger.info(f"Auto-closed {closed} orphan PaperTrade row(s)")
        return closed

    def sync_orders(self, limit: int = 50) -> int:
        """Pull recent orders from Alpaca and upsert into alpaca_orders.

        Returns count of new/updated orders.
        """
        orders = self.client.get_orders(status="all", limit=limit)
        count = 0

        for o in orders:
            if not o.get("ticker"):
                logger.warning(
                    "Skipping order %s with null ticker (likely MLEG parent "
                    "with no derivable underlying)",
                    o.get("order_id"),
                )
                continue
            existing = (
                self.db.query(AlpacaOrder)
                .filter_by(alpaca_order_id=o["order_id"])
                .first()
            )
            if existing:
                existing.status = o["status"]
                existing.filled_price = o["filled_price"]
                existing.filled_at = (
                    datetime.fromisoformat(o["filled_at"]) if o["filled_at"] else None
                )
                existing.synced_at = datetime.utcnow()
            else:
                self.db.add(AlpacaOrder(
                    alpaca_order_id=o["order_id"],
                    ticker=o["ticker"],
                    side=o["side"],
                    qty=o["qty"],
                    order_type=o["type"],
                    status=o["status"],
                    filled_price=o["filled_price"],
                    submitted_at=(
                        datetime.fromisoformat(o["submitted_at"]) if o["submitted_at"] else None
                    ),
                    filled_at=(
                        datetime.fromisoformat(o["filled_at"]) if o["filled_at"] else None
                    ),
                ))
                count += 1

        self.db.commit()
        logger.info(f"Synced {len(orders)} orders ({count} new)")
        return count

    def sync_account(self) -> dict:
        """Pull account summary from Alpaca.

        Returns account dict (equity, buying_power, cash, etc.).
        """
        return self.client.get_account()

    def get_portfolio_summary(self) -> dict:
        """Aggregated view: account info + position breakdown."""
        account = self.sync_account()
        positions = self.client.get_positions()

        total_unrealized = sum(p["unrealized_pl"] for p in positions)
        total_market_value = sum(abs(p["market_value"]) for p in positions)

        # Count paper trades too
        paper_open = self.db.query(PaperTrade).filter_by(status="open").count()

        return {
            "equity": account["equity"],
            "buying_power": account["buying_power"],
            "cash": account["cash"],
            "day_trade_count": account["day_trade_count"],
            "alpaca_positions": len(positions),
            "paper_positions": paper_open,
            "total_positions": len(positions) + paper_open,
            "total_market_value": total_market_value,
            "total_unrealized_pl": total_unrealized,
            "positions": positions,
        }
