"""Live valuation of open paper trades for the dashboard.

Computes a display `current_price` and `unrealized_pnl` for each open
PaperTrade. Both are best-effort: whatever can't be sourced comes back None and
the row shows "—" rather than failing the endpoint.

Sourcing strategy (most-accurate first):
- unrealized_pnl: prefer the broker's own mark-to-market. Paper trades are
  submitted to the Alpaca paper account, so `alpaca_positions` (synced every
  5 min) already carries a signed `unrealized_pl` per equity ticker and per
  option leg (keyed by OCC symbol). Summing the trade's legs there gives the
  real intraday P&L with no extra API calls. pair_short is the exception: its
  SPY hedge is pooled across every pair in one broker row and can't be split
  per-trade, so pair P&L is computed from live stock prices + the leg entries.
- current_price: the intraday underlying stock price (from a live-quote batch)
  for equity-style trades; the option mark (from the broker row) for single-leg
  options; None for multi-leg spreads where a single price is meaningless.

Callers build the two maps once per request and pass them in:
- positions_map: {symbol -> {"current_price": float|None, "unrealized_pl": float|None}}
- stock_prices:  {ticker -> float}  (live last-trade)
"""

from __future__ import annotations

import json
import logging

from src.db.models import PaperTrade
from src.services.order_mapper import build_occ_symbol

logger = logging.getLogger(__name__)

# Strategies whose position is one or more option legs.
_OPTION_STRATEGIES = {"options", "call_options", "spread", "bull_spread"}
_MULTI_LEG_STRATEGIES = {"spread", "bull_spread"}


def underlying_tickers(trades: list[PaperTrade]) -> set[str]:
    """Distinct equity tickers to fetch live prices for (underlyings + hedges)."""
    tickers: set[str] = set()
    for t in trades:
        if t.status != "open" or not t.ticker:
            continue
        tickers.add(t.ticker.upper())
        for leg in _stock_legs(t):
            lt = leg.get("ticker")
            if lt:
                tickers.add(str(lt).upper())
    return tickers


def value_open_trade(
    trade: PaperTrade,
    positions_map: dict[str, dict],
    stock_prices: dict[str, float],
) -> tuple[float | None, float | None]:
    """Return (current_price, unrealized_pnl) for one open trade.

    Never raises: any malformed leg or missing datum degrades to None.
    """
    try:
        if trade.strategy == "pair_short":
            return _value_pair_short(trade, stock_prices)
        if trade.strategy in _OPTION_STRATEGIES:
            return _value_option(trade, positions_map)
        # Default: single equity leg (long, legacy short, anything unknown).
        return _value_equity(trade, positions_map, stock_prices)
    except Exception as e:  # display path must not 500
        logger.warning(f"valuation failed for trade {trade.id} ({trade.strategy}): {e}")
        return None, None


# ── per-strategy valuation ───────────────────────────────────────────────

def _value_equity(
    trade: PaperTrade, positions_map: dict[str, dict], stock_prices: dict[str, float]
) -> tuple[float | None, float | None]:
    ticker = (trade.ticker or "").upper()
    live = stock_prices.get(ticker)
    pos = positions_map.get(ticker)

    current_price = live if live is not None else (pos or {}).get("current_price")

    pnl = (pos or {}).get("unrealized_pl")
    if pnl is None and current_price is not None and trade.entry_price:
        # Fallback: reconstruct from position_size when the broker row is absent.
        shares = _equity_shares(trade)
        if shares:
            direction = -1.0 if trade.direction == "short" else 1.0
            pnl = (current_price - trade.entry_price) * shares * direction
    return current_price, pnl


def _value_pair_short(
    trade: PaperTrade, stock_prices: dict[str, float]
) -> tuple[float | None, float | None]:
    # Compute from live prices + leg entries; broker hedge is pooled and can't
    # be attributed per pair. current_price is the short name's live price.
    ticker = (trade.ticker or "").upper()
    current_price = stock_prices.get(ticker)

    legs = _stock_legs(trade)
    if not legs:
        return current_price, None

    pnl = 0.0
    priced_any = False
    for leg in legs:
        lt = str(leg.get("ticker", "")).upper()
        entry = leg.get("entry")
        qty = leg.get("qty")
        live = stock_prices.get(lt)
        if live is None or entry is None or qty is None:
            continue
        priced_any = True
        if str(leg.get("leg")) == "short":
            pnl += (float(entry) - live) * float(qty)
        else:  # hedge (long)
            pnl += (live - float(entry)) * float(qty)
    return current_price, (pnl if priced_any else None)


def _value_option(
    trade: PaperTrade, positions_map: dict[str, dict]
) -> tuple[float | None, float | None]:
    legs = _option_legs_with_meta(trade)
    if not legs:
        return None, None

    pnl = 0.0
    pnl_matched = 0
    net_mark = 0.0
    mark_matched = 0
    for occ, action in legs:
        pos = positions_map.get(occ)
        if not pos:
            continue
        if pos.get("unrealized_pl") is not None:
            pnl += pos["unrealized_pl"]
            pnl_matched += 1
        if pos.get("current_price") is not None:
            # Long leg (buy) adds value, short leg (sell) subtracts: the net
            # premium to liquidate the spread. Positive = net debit, negative
            # = net credit.
            sign = 1.0 if action == "buy" else -1.0
            net_mark += sign * pos["current_price"]
            mark_matched += 1

    unrealized = pnl if pnl_matched else None

    if trade.strategy in _MULTI_LEG_STRATEGIES:
        # Net spread mark across the legs (None if no leg priced → row shows "—",
        # never falls back to the underlying stock price).
        current_price = net_mark if mark_matched else None
    else:
        # Single-leg option: the option's own mark.
        current_price = (positions_map.get(legs[0][0]) or {}).get("current_price")
    return current_price, unrealized


# ── helpers ──────────────────────────────────────────────────────────────

def _equity_shares(trade: PaperTrade) -> float:
    if not trade.entry_price:
        return 0.0
    # short carries 1.5x margin in position_size (mirrors legacy _to_response).
    denom = trade.entry_price * (1.5 if trade.direction == "short" else 1.0)
    return (trade.position_size or 0) / denom if denom else 0.0


def _option_legs_with_meta(trade: PaperTrade) -> list[tuple[str, str]]:
    """(occ_symbol, action) per option leg. From legs_json if present, else the
    single-leg strike/type/expiry fields. action is 'buy' (long) or 'sell'
    (short); single bought options default to 'buy'."""
    legs: list[tuple[str, str]] = []
    for leg in _load_legs(trade):
        otype = leg.get("option_type")
        strike = leg.get("strike")
        if otype and strike is not None and trade.expiry:
            try:
                occ = build_occ_symbol(trade.ticker, trade.expiry, str(otype), float(strike))
            except (ValueError, TypeError):
                continue
            action = str(leg.get("action", "buy")).strip().lower()
            legs.append((occ, "sell" if action == "sell" else "buy"))
    if legs:
        return legs
    if trade.option_type and trade.strike and trade.expiry:
        try:
            return [(build_occ_symbol(trade.ticker, trade.expiry, trade.option_type, trade.strike), "buy")]
        except (ValueError, TypeError):
            return []
    return []


def spread_entry_mark(trade: PaperTrade) -> float | None:
    """Net entry premium of a multi-leg spread from its leg premiums, using the
    same buy=+ / sell=- convention as the live net mark, so the entry and
    current columns are comparable. None for non-spreads or missing premiums.

    (The stored entry_price on spread trades is the underlying stock price at
    entry, not the net premium, so it can't be shown next to a premium mark.)"""
    if trade.strategy not in _MULTI_LEG_STRATEGIES:
        return None
    net = 0.0
    priced = 0
    for leg in _load_legs(trade):
        premium = leg.get("premium")
        if premium is None:
            continue
        sign = 1.0 if str(leg.get("action", "")).strip().lower() == "buy" else -1.0
        net += sign * float(premium)
        priced += 1
    return net if priced else None


def uses_underlying_price(strategy: str) -> bool:
    """Whether current_price for this strategy is the underlying stock price
    (so a daily-close fallback is meaningful). False for option strategies,
    whose current_price is an option/spread mark — the underlying close would
    be a misleading number in the entry/current column."""
    return strategy not in _OPTION_STRATEGIES


def _load_legs(trade: PaperTrade) -> list[dict]:
    if not trade.legs_json:
        return []
    try:
        raw = json.loads(trade.legs_json)
    except (json.JSONDecodeError, TypeError):
        return []
    return [leg for leg in raw if isinstance(leg, dict)] if isinstance(raw, list) else []


def _stock_legs(trade: PaperTrade) -> list[dict]:
    """Stock legs (pair_short: short + hedge). Distinguished from option legs by
    carrying a 'ticker' field."""
    return [leg for leg in _load_legs(trade) if "ticker" in leg]
