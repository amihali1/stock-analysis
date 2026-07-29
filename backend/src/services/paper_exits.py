"""Close open PaperTrade rows on stop/target breach or option expiry.

PaperTrade rows were written at submission time but nothing ever closed
them (audit 2026-07-02: 4 trades open since 6/09 with expiries long past).
Without closes there is no realized P&L, and paper_validation.py — the
live-readiness scoreboard — has nothing to measure.

Exit rules per strategy:

- Stock (``long``, ``short``): close when the latest close crosses
  stop_loss or target_price. P&L from the underlying move over
  position_size/entry_price shares.
- Single-leg options (``options`` = puts, ``call_options`` = calls):
  close at/after expiry at intrinsic value; P&L = intrinsic - premium
  paid (position_size). No pre-expiry exits — mid-life option value
  needs a pricing model we don't run here.
- Spreads (``spread``, ``bull_spread``): close at/after expiry. Exit
  value per leg is intrinsic × sign (buy = owned, sell = owed); entry
  cash flow per leg comes from the premiums stored in legs_json. Works
  for both debit and credit structures.

Evaluated daily after the morning price fetch, so "latest close" is the
prior trading day. That lag is acceptable for paper accounting.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import PaperTrade, PriceHistory

logger = logging.getLogger(__name__)


def _latest_close(db: Session, ticker: str) -> float | None:
    row = (
        db.query(PriceHistory)
        .filter(PriceHistory.ticker == ticker, PriceHistory.close.isnot(None))
        .order_by(PriceHistory.date.desc())
        .first()
    )
    return float(row.close) if row else None


def _intrinsic(option_type: str, strike: float, underlying: float) -> float:
    if option_type == "call":
        return max(0.0, underlying - strike)
    return max(0.0, strike - underlying)


def _close(trade: PaperTrade, exit_price: float, pnl: float, reason: str) -> dict:
    trade.status = "closed"
    trade.exit_price = round(exit_price, 4)
    trade.pnl = round(pnl, 2)
    trade.closed_at = datetime.utcnow()
    logger.info(
        "paper_exit: %s %s closed (%s) exit=%.2f pnl=%.2f",
        trade.ticker, trade.strategy, reason, exit_price, pnl,
    )
    return {
        "id": trade.id, "ticker": trade.ticker, "strategy": trade.strategy,
        "status": "closed", "reason": reason, "pnl": trade.pnl,
    }


def _sessions_open(db: Session, trade: PaperTrade) -> int:
    """Trading sessions elapsed since the trade opened (price rows after open date)."""
    if trade.opened_at is None:
        return 0
    return (
        db.query(PriceHistory)
        .filter(
            PriceHistory.ticker == trade.ticker,
            PriceHistory.date > trade.opened_at.date(),
            PriceHistory.close.isnot(None),
        )
        .count()
    )


# ---------------------------------------------------------------------------
# Valuation — mark a trade to market at a given underlying close.
#
# These are the single source of P&L math. Both the exit evaluators (which
# add the stop/target/time/expiry *decision* on top) and the orphan-close
# pricer (portfolio_sync, which has no decision — the broker already closed
# the legs) call them. Options/spreads use intrinsic value only: no time
# value, matching how expiry exits are priced. A defined approximation when
# marking a still-open position, but far better than leaving pnl NULL.
# ---------------------------------------------------------------------------


def _value_stock(trade: PaperTrade, current: float) -> float | None:
    """P&L for a plain long/short stock trade at ``current``."""
    entry = trade.entry_price or 0.0
    if entry <= 0 or not trade.position_size:
        return None
    # size_short persists position_size as MARGIN (1.5x notional); size_long
    # persists plain notional. Back out actual shares accordingly.
    notional = trade.position_size / (1.5 if trade.strategy == "short" else 1.0)
    shares = notional / entry
    is_long = trade.strategy == "long"
    return shares * (current - entry) if is_long else shares * (entry - current)


def _value_single_leg(trade: PaperTrade, current: float) -> float | None:
    """Intrinsic P&L for a single-leg long option (debit = position_size)."""
    if trade.strike is None:
        return None
    option_type = "call" if trade.strategy == "call_options" else "put"
    if trade.option_type in ("call", "put"):
        option_type = trade.option_type
    contracts = trade.contracts or 1
    value = _intrinsic(option_type, trade.strike, current) * 100 * contracts
    return value - (trade.position_size or 0.0)


def _value_spread(trade: PaperTrade, current: float) -> float | None:
    """Intrinsic P&L for a multi-leg spread using legs_json premiums."""
    if not trade.legs_json:
        return None
    try:
        legs = json.loads(trade.legs_json)
    except (ValueError, TypeError):
        return None

    pnl = 0.0
    for leg in legs:
        sign = 1.0 if leg.get("action") == "buy" else -1.0
        contracts = leg.get("contracts") or trade.contracts or 1
        strike = leg.get("strike")
        option_type = leg.get("option_type", "put")
        premium = leg.get("premium") or 0.0
        if strike is None:
            return None
        exit_value = sign * _intrinsic(option_type, strike, current) * 100 * contracts
        entry_cash = -sign * premium * 100 * contracts  # buy = cash out, sell = cash in
        pnl += exit_value + entry_cash
    return pnl


def _value_pair(trade: PaperTrade, db: Session, current: float) -> float | None:
    """P&L for a pair_short: short leg gains as pick falls, hedge as it rises."""
    try:
        legs = json.loads(trade.legs_json or "[]")
        short_leg = next(l for l in legs if l["leg"] == "short")
        hedge_leg = next(l for l in legs if l["leg"] == "hedge")
    except (ValueError, KeyError, StopIteration):
        return None
    hedge_cur = _latest_close(db, hedge_leg["ticker"])
    if hedge_cur is None:
        return None
    return (
        float(short_leg["qty"]) * (float(short_leg["entry"]) - current)
        + float(hedge_leg["qty"]) * (hedge_cur - float(hedge_leg["entry"]))
    )


def mark_to_market(db: Session, trade: PaperTrade, current: float) -> tuple[float, float] | None:
    """Value any open trade at underlying close ``current``, independent of
    exit rules. Returns ``(exit_price, pnl)`` or ``None`` if unpriceable
    (missing entry/strike/legs). Used by the orphan-close sweep so
    broker-closed trades land with real P&L instead of NULL.
    """
    strategy = trade.strategy
    if strategy in ("long", "short"):
        pnl = _value_stock(trade, current)
    elif strategy == "pair_short":
        pnl = _value_pair(trade, db, current)
    elif strategy in ("options", "call_options"):
        pnl = _value_single_leg(trade, current)
    elif strategy in ("spread", "bull_spread"):
        pnl = _value_spread(trade, current)
    else:
        return None
    if pnl is None:
        return None
    return current, pnl


def _evaluate_pair(trade: PaperTrade, db: Session, current: float, time_up: bool) -> dict | None:
    """pair_short: close on short-leg stop/target cross or time expiry."""
    breach: str | None = None
    if trade.stop_loss is not None and current >= trade.stop_loss:
        breach = f"stop hit ({current:.2f} >= {trade.stop_loss:.2f})"
    elif trade.target_price is not None and current <= trade.target_price:
        breach = f"target hit ({current:.2f} <= {trade.target_price:.2f})"
    elif time_up:
        breach = "time exit"
    if breach is None:
        return None

    pnl = _value_pair(trade, db, current)
    if pnl is None:
        logger.warning("paper_exit: pair %s unpriceable (bad legs/hedge), skipping", trade.id)
        return None
    return _close(trade, current, pnl, breach)


def _evaluate_stock(trade: PaperTrade, current: float, time_up: bool = False) -> dict | None:
    """Stop/target/time exit for plain long/short stock trades."""
    entry = trade.entry_price or 0.0
    if entry <= 0 or not trade.position_size:
        return None
    is_long = trade.strategy == "long"

    breach: str | None = None
    if is_long:
        if trade.stop_loss is not None and current <= trade.stop_loss:
            breach = f"stop hit ({current:.2f} <= {trade.stop_loss:.2f})"
        elif trade.target_price is not None and current >= trade.target_price:
            breach = f"target hit ({current:.2f} >= {trade.target_price:.2f})"
    else:
        if trade.stop_loss is not None and current >= trade.stop_loss:
            breach = f"stop hit ({current:.2f} >= {trade.stop_loss:.2f})"
        elif trade.target_price is not None and current <= trade.target_price:
            breach = f"target hit ({current:.2f} <= {trade.target_price:.2f})"

    if breach is None and time_up:
        breach = "time exit"
    if breach is None:
        return None
    pnl = _value_stock(trade, current)
    if pnl is None:
        return None
    return _close(trade, current, pnl, breach)


def _evaluate_catastrophic(trade: PaperTrade, db: Session, current: float) -> dict | None:
    """Early exit for option/spread positions before expiry, by strategy.

    Options and spreads otherwise only exit at expiry (no stop/target), so a
    large move against the position rides to near-total loss unmanaged.

    Two triggers, because the same rule does not fit both shapes:

    * Single-leg long options — UNDERLYING move (>= catastrophic_underlying_move
      against entry). Their mark-to-market is intrinsic only, which shows an
      out-of-the-money long option at a full loss from day 1, so an MTM-based
      trigger would fire spuriously. The underlying move is time-value-free.

    * Spreads — MARK-TO-MARKET loss as a fraction of max_loss (>=
      catastrophic_spread_loss_fraction). A defined-risk spread reaches max
      loss at a fairly small underlying move, so the underlying-move trigger
      only fired once the spread was already at ~max loss (observed 2026-07-29:
      4 bull_spreads booked exactly -max_loss). The MTM-vs-max_loss trigger
      cuts at a chosen fraction (e.g. 60%) BEFORE max loss and frees the
      capital. Spread MTM does not have the OTM full-loss artifact — the two
      legs net out — so it is a usable signal here. Booked P&L uses
      mark_to_market, matching the expiry/orphan intrinsic convention.

    Runs at batch cadence (close price): catches a large close-to-close move,
    not an intra-session spike that recovers by the close.
    """
    settings = get_settings()

    if trade.strategy in ("spread", "bull_spread"):
        frac = settings.catastrophic_spread_loss_fraction
        if frac <= 0 or not trade.max_loss or trade.max_loss <= 0:
            return None
        marked = mark_to_market(db, trade, current)
        if marked is None:
            return None
        _, pnl = marked
        if pnl > -frac * trade.max_loss:  # loss not yet deep enough
            return None
        return _close(
            trade, current, pnl,
            f"catastrophic spread loss {pnl:.0f} (>= {frac:.0%} of {trade.max_loss:.0f} max)",
        )

    # Single-leg long options: underlying-move trigger.
    f = settings.catastrophic_underlying_move
    if f <= 0:
        return None
    entry = trade.entry_price or 0.0
    if entry <= 0:
        return None
    # direction "long" = bullish exposure (call_options) → hurt by a falling
    # underlying; "short" = bearish (options/puts) → hurt by a rise.
    bullish = trade.direction == "long"
    adverse = current <= entry * (1 - f) if bullish else current >= entry * (1 + f)
    if not adverse:
        return None
    marked = mark_to_market(db, trade, current)
    if marked is None:
        return None
    _, pnl = marked
    move = current / entry - 1
    return _close(trade, current, pnl, f"catastrophic underlying move {move:+.1%}")


def _evaluate_single_leg(trade: PaperTrade, current: float, today: date) -> dict | None:
    """Expiry exit for single-leg long options (debit = position_size)."""
    if trade.expiry is None or today < trade.expiry:
        return None
    if trade.strike is None:
        logger.warning("paper_exit: %s single-leg without strike, skipping", trade.id)
        return None
    pnl = _value_single_leg(trade, current)
    if pnl is None:
        return None
    return _close(trade, current, pnl, f"expired {trade.expiry.isoformat()}")


def _evaluate_spread(trade: PaperTrade, current: float, today: date) -> dict | None:
    """Expiry exit for multi-leg spreads using legs_json premiums."""
    if trade.expiry is None or today < trade.expiry:
        return None
    if not trade.legs_json:
        logger.warning("paper_exit: %s spread without legs_json, skipping", trade.id)
        return None
    pnl = _value_spread(trade, current)
    if pnl is None:
        logger.warning("paper_exit: %s spread unpriceable (bad legs/strike), skipping", trade.id)
        return None
    return _close(trade, current, pnl, f"expired {trade.expiry.isoformat()}")


def evaluate_paper_exits(db: Session, today: date | None = None) -> list[dict]:
    """Evaluate all open paper trades, close the ones that hit an exit rule.

    Returns one dict per open trade evaluated (closed, held, or skipped).
    Commits once at the end; a per-trade failure rolls back only that
    trade's changes and moves on.
    """
    today = today or date.today()
    open_trades = db.query(PaperTrade).filter(PaperTrade.status == "open").all()
    results: list[dict] = []

    for trade in open_trades:
        try:
            current = _latest_close(db, trade.ticker)
            if current is None:
                results.append({
                    "id": trade.id, "ticker": trade.ticker,
                    "status": "skipped", "reason": "no price data",
                })
                continue

            if trade.strategy in ("long", "short", "pair_short"):
                time_up = _sessions_open(db, trade) >= get_settings().time_exit_sessions
                if trade.strategy == "pair_short":
                    outcome = _evaluate_pair(trade, db, current, time_up)
                else:
                    outcome = _evaluate_stock(trade, current, time_up)
            elif trade.strategy in ("options", "call_options"):
                outcome = (
                    _evaluate_catastrophic(trade, db, current)
                    or _evaluate_single_leg(trade, current, today)
                )
            elif trade.strategy in ("spread", "bull_spread"):
                outcome = (
                    _evaluate_catastrophic(trade, db, current)
                    or _evaluate_spread(trade, current, today)
                )
            else:
                results.append({
                    "id": trade.id, "ticker": trade.ticker,
                    "status": "skipped", "reason": f"unknown strategy {trade.strategy}",
                })
                continue

            if outcome is not None:
                db.commit()
                results.append(outcome)
            else:
                results.append({
                    "id": trade.id, "ticker": trade.ticker,
                    "status": "held", "current": current,
                })
        except Exception:
            db.rollback()
            logger.exception("paper_exit: evaluation failed for trade %s", trade.id)
            results.append({
                "id": trade.id, "ticker": trade.ticker,
                "status": "error",
            })

    return results
