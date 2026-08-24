"""Live valuation of open paper trades (dashboard current_price + unrealized_pnl).

Locks the per-strategy sourcing in paper_trade_valuation.value_open_trade:
- long: broker mark-to-market preferred, position_size fallback otherwise
- pair_short: computed from live stock prices + leg entries (hedge is pooled)
- single-leg option: broker P&L + option mark by OCC symbol
- multi-leg spread: summed leg P&L, no single current_price
"""

from __future__ import annotations

import json
from datetime import date, datetime

from src.db.models import PaperTrade
from src.services.order_mapper import build_occ_symbol
from src.services.paper_trade_valuation import underlying_tickers, value_open_trade

EXPIRY = date(2026, 9, 18)


def _trade(**kw) -> PaperTrade:
    defaults = dict(
        ticker="TEST", direction="long", strategy="long", status="open",
        entry_price=100.0, opened_at=datetime(2026, 8, 1),
    )
    defaults.update(kw)
    return PaperTrade(**defaults)


def test_long_prefers_broker_mtm():
    trade = _trade(ticker="AAPL", strategy="long", entry_price=190.0)
    positions = {"AAPL": {"current_price": 205.0, "unrealized_pl": 123.45}}
    prices = {"AAPL": 205.0}

    current, pnl = value_open_trade(trade, positions, prices)

    assert current == 205.0  # live price wins for the display column
    assert pnl == 123.45  # broker P&L used verbatim, not recomputed


def test_long_fallback_from_position_size():
    # No broker row: reconstruct P&L from position_size / entry.
    trade = _trade(ticker="MSFT", strategy="long", entry_price=100.0, position_size=1000.0)
    current, pnl = value_open_trade(trade, {}, {"MSFT": 110.0})

    assert current == 110.0
    # 10 shares ($1000 / $100), up $10 => +$100
    assert pnl == 100.0


def test_pair_short_from_live_prices():
    legs = [
        {"leg": "short", "ticker": "XYZ", "qty": 10, "entry": 50.0},
        {"leg": "hedge", "ticker": "SPY", "qty": 1, "entry": 500.0},
    ]
    trade = _trade(ticker="XYZ", direction="short", strategy="pair_short",
                   legs_json=json.dumps(legs))
    prices = {"XYZ": 45.0, "SPY": 510.0}  # short down $5 (good), hedge up $10

    current, pnl = value_open_trade(trade, {}, prices)

    assert current == 45.0  # short name's live price
    # short: (50-45)*10 = +50 ; hedge: (510-500)*1 = +10 => +60
    assert pnl == 60.0


def test_single_leg_option_uses_occ_broker_mark():
    trade = _trade(ticker="NVDA", strategy="call_options", entry_price=5.0,
                   option_type="call", strike=150.0, expiry=EXPIRY)
    occ = build_occ_symbol("NVDA", EXPIRY, "call", 150.0)
    positions = {occ: {"current_price": 8.5, "unrealized_pl": 350.0}}

    current, pnl = value_open_trade(trade, positions, {})

    assert current == 8.5  # option mark surfaced for single-leg
    assert pnl == 350.0


def test_bull_spread_sums_legs_no_single_price():
    legs = [
        {"option_type": "put", "action": "sell", "strike": 90.0, "contracts": 1},
        {"option_type": "put", "action": "buy", "strike": 85.0, "contracts": 1},
    ]
    trade = _trade(ticker="SOFI", strategy="bull_spread", entry_price=1.0,
                   expiry=EXPIRY, legs_json=json.dumps(legs))
    sell_occ = build_occ_symbol("SOFI", EXPIRY, "put", 90.0)
    buy_occ = build_occ_symbol("SOFI", EXPIRY, "put", 85.0)
    positions = {
        sell_occ: {"current_price": 0.40, "unrealized_pl": 60.0},
        buy_occ: {"current_price": 0.20, "unrealized_pl": -20.0},
    }

    current, pnl = value_open_trade(trade, positions, {})

    assert current is None  # multi-leg: no single meaningful price
    assert pnl == 40.0  # 60 + (-20)


def test_option_pnl_none_when_no_broker_match():
    trade = _trade(ticker="LRCX", strategy="call_options", entry_price=5.0,
                   option_type="call", strike=1000.0, expiry=EXPIRY)
    current, pnl = value_open_trade(trade, {}, {})
    assert current is None
    assert pnl is None


def test_underlying_tickers_includes_hedges():
    legs = [
        {"leg": "short", "ticker": "xyz", "qty": 10, "entry": 50.0},
        {"leg": "hedge", "ticker": "spy", "qty": 1, "entry": 500.0},
    ]
    trades = [
        _trade(ticker="AAPL", strategy="long"),
        _trade(ticker="XYZ", strategy="pair_short", legs_json=json.dumps(legs)),
        _trade(ticker="OLD", strategy="long", status="closed"),  # excluded
    ]
    assert underlying_tickers(trades) == {"AAPL", "XYZ", "SPY"}
