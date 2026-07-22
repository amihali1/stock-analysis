"""Sync positions, orders, and account data from Alpaca into local DB."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import AlpacaOrder, AlpacaPosition, PaperTrade
from src.services.alpaca_client import AlpacaClient, _underlying_from_occ
from src.services.paper_exits import _latest_close, mark_to_market

logger = logging.getLogger(__name__)

# Residue alerts throttle: once per (underlying, calendar day). Module state —
# a backend restart may re-alert once, which is acceptable for a daily ping.
_residue_alerted: dict[str, date] = {}

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

        # Underlyings that ever actually filled at the broker. A trade only
        # gets marked-to-market on close if it was really held: an order that
        # only ever reached `expired`/`canceled`/`rejected` never became a
        # position, so pricing it at intrinsic would inject fictional P&L into
        # the live-gate. Fills can age out of the sync window; absence of a
        # fill row means "leave pnl NULL", which is the safe default (2026-07-22
        # audit: all 8 orphan-closed bull_spreads had expired MLEG orders).
        ever_filled: set[str] = set()
        for order in (
            self.db.query(AlpacaOrder)
            .filter(AlpacaOrder.status.in_(("filled", "partially_filled")))
            .all()
        ):
            u = _to_underlying(order.ticker)
            if u:
                ever_filled.add(u)

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
                # Price the trade at its latest underlying close instead of
                # leaving pnl NULL — but ONLY if it was ever really held.
                # Multi-leg spreads reach the broker exit before their
                # expiry-based paper_exit fires, so without this every closed
                # spread produced zero live-gate evidence (2026-07-21). Marks
                # use intrinsic value (no time value) — same model as the
                # expiry exits, a defined approximation. Never-filled orders
                # stay NULL so their non-existent P&L never reaches the gate.
                if pt.ticker in ever_filled:
                    current = _latest_close(self.db, pt.ticker)
                    priced = mark_to_market(self.db, pt, current) if current is not None else None
                    if priced is not None:
                        exit_price, pnl = priced
                        pt.exit_price = round(exit_price, 4)
                        pt.pnl = round(pnl, 2)
                logger.warning(
                    "Auto-closed orphan PaperTrade %s (%s %s) — no position/order "
                    "since %s. pnl=%s",
                    pt.id, pt.ticker, pt.strategy, pt.orphan_seen_at.isoformat(),
                    f"{pt.pnl:.2f}" if pt.pnl is not None else "NULL (unpriceable)",
                )
        self.db.commit()
        if closed:
            logger.info(f"Auto-closed {closed} orphan PaperTrade row(s)")
        return closed

    def detect_residue_positions(self, now: datetime | None = None) -> list[dict]:
        """Flag broker positions that no strategy owns.

        Inverse of the orphan sweep: an ITM single-leg option exercises into
        stock at expiry (or a short leg gets assigned), the option PaperTrade
        closes, and the resulting share position belongs to nobody — but the
        scheduler's open-position deduction still counts it against
        daily_capital_cap. 2026-07-14: 300 exercised DOCU shares ($14.8k)
        consumed 60% of the cap and silently halted rec generation.

        A position's underlying is "owned" when an open PaperTrade carries it,
        an in-flight order references it, or it is the pair-hedge symbol while
        any pair_short trade is open (hedge legs live under the hedge symbol,
        not the trade's ticker). Everything else is residue: log + ntfy alert,
        throttled to once per underlying per day. Detection only — liquidation
        stays a human decision.
        """
        now = now or datetime.utcnow()
        owned: set[str] = set()
        open_trades = self.db.query(PaperTrade).filter_by(status="open").all()
        for pt in open_trades:
            owned.add(pt.ticker)
        if any(pt.strategy == "pair_short" for pt in open_trades):
            owned.add(get_settings().pair_hedge_symbol)
        for order in (
            self.db.query(AlpacaOrder)
            .filter(AlpacaOrder.status.in_(_ALPACA_OPEN_ORDER_STATUSES))
            .all()
        ):
            u = _to_underlying(order.ticker)
            if u:
                owned.add(u)

        residue: list[dict] = []
        for pos in self.db.query(AlpacaPosition).all():
            u = _to_underlying(pos.ticker)
            if u and u not in owned:
                residue.append({
                    "ticker": pos.ticker,
                    "underlying": u,
                    "qty": pos.qty,
                    "market_value": pos.market_value,
                })

        for r in residue:
            logger.warning(
                "Residue position: %s qty=%s ($%.0f) has no open PaperTrade or "
                "in-flight order — counts against the capital cap until closed.",
                r["ticker"], r["qty"], r["market_value"] or 0,
            )
            if _residue_alerted.get(r["underlying"]) != now.date():
                _residue_alerted[r["underlying"]] = now.date()
                self._send_ntfy(
                    f"Unowned broker position: {r['ticker']} qty={r['qty']} "
                    f"(${(r['market_value'] or 0):,.0f}). Likely option exercise/"
                    f"assignment residue — eats the capital cap until closed."
                )
        return residue

    @staticmethod
    def _send_ntfy(message: str) -> None:
        topic = get_settings().ntfy_topic
        if not topic:
            return
        try:
            httpx.post(
                topic,
                content=message,
                headers={
                    "Title": "stock-analysis residue position",
                    "Priority": "high",
                    "Tags": "warning,moneybag",
                },
                timeout=10,
            )
        except Exception:
            logger.exception("ntfy residue alert failed")

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
