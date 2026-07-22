"""End-to-end trade lifecycle integration tests.

Unit tests cover each service in isolation with its own mock. These drive a
trade across the SEAMS that connect them — execution_engine → portfolio_sync
→ paper_exits → live_gate — over ONE shared in-memory DB, with a FakeBroker
whose positions/orders evolve between sync calls the way Alpaca's do.

Each scenario maps to a production incident the isolated unit tests could not
have caught, because the bug lived in the coupling (a PaperTrade row persisted
at submit whose fate depends on whether the broker actually filled):

  1. filled spread   -> orphan-close prices real pnl -> counts in gate  (e121a24)
  2. unfilled spread -> orphan-close leaves pnl NULL -> excluded         (5df6ba2)
  3. submit->fill race: in-flight order protects the trade until grace   (2026-07-07)
  4. full round trip: rec -> fill -> exit -> live_gate sees the evidence
  5. spread = 1 position not N legs against the position cap             (2026-06-09)
  6. option closes -> leftover stock = residue flagged                  (2026-07-14)
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import (
    AlpacaOrder,
    AlpacaPosition,
    Base,
    PaperTrade,
    PriceHistory,
    Recommendation,
    Stock,
)
from src.services.execution_engine import ExecutionEngine
from src.services.live_gate import ArmSpec, evaluate_arm
from src.services.order_mapper import OrderMapper
from src.services.paper_exits import evaluate_paper_exits
from src.services.portfolio_sync import PortfolioSync
from src.services.safety_rails import TradingSafetyRails

T0 = datetime(2026, 7, 20, 15, 0, 0)
PAST_GRACE = T0 + timedelta(minutes=PortfolioSync.ORPHAN_GRACE_MINUTES)


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class FakeBroker:
    """Scriptable stand-in for AlpacaClient. Tests mutate ``.positions`` and
    ``.orders`` between phases to model the broker's state changing over time
    (order accepted -> filled -> position appears -> position exits)."""

    def __init__(self, positions=None, orders=None, buying_power=50000.0):
        self.positions = positions or []
        self.orders = orders or []
        self.buying_power = buying_power
        self.submitted: list[dict] = []

    # --- reads ---
    def is_market_open(self):
        return True

    def get_account(self):
        return {
            "equity": 50000.0, "buying_power": self.buying_power,
            "cash": 30000.0, "day_trade_count": 0,
        }

    def get_positions(self):
        return list(self.positions)

    def get_orders(self, status="all", limit=50):
        return list(self.orders)

    def get_option_quotes(self, occ_symbols):
        return {}

    # --- writes ---
    def _record(self, **kw):
        oid = f"fake-{len(self.submitted) + 1}"
        self.submitted.append({"order_id": oid, **kw})
        return {"order_id": oid}

    def submit_order(self, **kw):
        return self._record(kind="order", **kw)

    def submit_bracket_order(self, **kw):
        return self._record(kind="bracket", **kw)

    def submit_spread_order(self, **kw):
        return self._record(kind="mleg", **kw)

    def close_position(self, ticker):
        return {"ticker": ticker, "status": "closing", "order_id": "close-1"}


def _position(ticker, qty, side="long", price=100.0, mkt=None):
    return {
        "ticker": ticker, "qty": qty, "side": side,
        "avg_entry_price": price, "current_price": price,
        "market_value": mkt if mkt is not None else qty * price,
        "unrealized_pl": 0.0, "unrealized_plpc": 0.0, "change_today": 0.0,
    }


def _order(ticker, status, side="buy", qty=1.0, filled_price=None):
    return {
        "order_id": f"ord-{ticker}-{status}", "ticker": ticker, "side": side,
        "qty": qty, "type": "limit", "status": status,
        "filled_price": filled_price, "limit_price": None, "stop_price": None,
        "filled_qty": qty if status == "filled" else 0.0,
        "submitted_at": "2026-07-20T09:30:00",
        "filled_at": "2026-07-20T09:31:00" if status == "filled" else None,
    }


def _bull_spread_legs():
    """Call debit spread: buy 530C, sell 555C. Net debit 7.94/share."""
    return [
        {"action": "buy", "strike": 530.0, "option_type": "call", "premium": 42.99, "contracts": 1},
        {"action": "sell", "strike": 555.0, "option_type": "call", "premium": 35.05, "contracts": 1},
    ]


def _seed_price(db, ticker, close, on=date(2026, 7, 20)):
    db.add(Stock(ticker=ticker))
    db.add(PriceHistory(ticker=ticker, date=on, close=close))
    db.commit()


def _sweep(sync, at):
    sync.sync_positions()
    sync.sync_orders()
    return sync.close_orphan_paper_trades(now=at)


# ---------------------------------------------------------------------------
# 1. Filled spread reaches the broker exit before expiry -> priced on close.
# ---------------------------------------------------------------------------
def test_filled_spread_orphan_close_is_priced_and_counts_in_gate():
    db = _make_db()
    _seed_price(db, "AMD", 560.0)  # above both call strikes -> spread at max value
    db.add(PaperTrade(
        ticker="AMD", strategy="bull_spread", status="open", entry_price=7.94,
        position_size=794.0, legs_json=json.dumps(_bull_spread_legs()),
        contracts=1, expiry=date(2026, 8, 7), opened_at=T0 - timedelta(days=1),
    ))
    db.commit()

    # Broker: legs filled (a real position existed), then the position exits
    # and vanishes before the 8/07 expiry-based paper_exit could fire.
    broker = FakeBroker(
        positions=[],
        orders=[_order("AMD260807C00530000", "filled", side="buy", filled_price=42.99)],
    )
    sync = PortfolioSync(db, client=broker)
    _sweep(sync, T0)                    # first sighting: stamp only
    closed = _sweep(sync, PAST_GRACE)  # past grace: close + price

    assert closed == 1
    pt = db.query(PaperTrade).filter_by(ticker="AMD").one()
    assert pt.status == "closed"
    assert pt.pnl is not None                 # real evidence, not NULL
    assert pt.pnl == 1706.0                   # width - debit, both calls ITM

    # It now counts toward the bull_credit gate arm.
    spec = ArmSpec(name="bull_credit", strategies=("bull_spread",),
                   baseline=date(2026, 7, 1))
    res = evaluate_arm(db, spec, today=date(2026, 8, 15))
    assert res.closed_trades == 1
    assert res.mean_return is not None


# ---------------------------------------------------------------------------
# 2. Spread whose MLEG order only ever expired -> never a position -> NULL.
#    (The guard that keeps fictional pnl out of the gate.)
# ---------------------------------------------------------------------------
def test_unfilled_spread_orphan_close_stays_null_and_excluded():
    db = _make_db()
    _seed_price(db, "AMD", 560.0)
    db.add(PaperTrade(
        ticker="AMD", strategy="bull_spread", status="open", entry_price=7.94,
        position_size=794.0, legs_json=json.dumps(_bull_spread_legs()),
        contracts=1, expiry=date(2026, 8, 7), opened_at=T0 - timedelta(days=1),
    ))
    db.commit()

    # Order NEVER filled — expired. No position ever existed.
    broker = FakeBroker(
        positions=[],
        orders=[_order("AMD260807C00530000", "expired", side="buy")],
    )
    sync = PortfolioSync(db, client=broker)
    _sweep(sync, T0)
    closed = _sweep(sync, PAST_GRACE)

    assert closed == 1
    pt = db.query(PaperTrade).filter_by(ticker="AMD").one()
    assert pt.status == "closed"
    assert pt.pnl is None                     # fiction kept out

    spec = ArmSpec(name="bull_credit", strategies=("bull_spread",),
                   baseline=date(2026, 7, 1))
    res = evaluate_arm(db, spec, today=date(2026, 8, 15))
    assert res.closed_trades == 0             # NULL row excluded from evidence


# ---------------------------------------------------------------------------
# 3. Submit->fill race: an in-flight order protects the trade until it either
#    fills (stays open) or the grace window elapses with no position/order.
# ---------------------------------------------------------------------------
def test_in_flight_order_protects_trade_through_grace():
    db = _make_db()
    _seed_price(db, "MU", 920.0)
    db.add(PaperTrade(
        ticker="MU", strategy="long", status="open", entry_price=920.0,
        position_size=920.0, opened_at=T0 - timedelta(minutes=1),
    ))
    db.commit()

    broker = FakeBroker(positions=[], orders=[_order("MU", "new")])  # working
    sync = PortfolioSync(db, client=broker)
    _sweep(sync, T0)
    closed = _sweep(sync, PAST_GRACE)  # still in-flight past grace

    assert closed == 0
    pt = db.query(PaperTrade).filter_by(ticker="MU").one()
    assert pt.status == "open"
    assert pt.orphan_seen_at is None           # in-flight order clears the stamp


# ---------------------------------------------------------------------------
# 4. Full round trip: recommendation -> execute -> broker fill -> price-based
#    exit -> the closed trade becomes live_gate evidence.
# ---------------------------------------------------------------------------
def _settings(**overrides):
    from unittest.mock import MagicMock
    s = MagicMock()
    s.alpaca_trading_enabled = overrides.get("alpaca_trading_enabled", True)
    s.auto_execute_enabled = overrides.get("auto_execute_enabled", True)
    s.min_score_threshold = overrides.get("min_score_threshold", 0.7)
    s.min_score_threshold_bear = overrides.get("min_score_threshold_bear", 0.30)
    s.trading_mode = overrides.get("trading_mode", "paper")
    s.max_daily_loss = overrides.get("max_daily_loss", 500.0)
    s.max_open_positions = overrides.get("max_open_positions", 20)
    s.max_position_size = overrides.get("max_position_size", 1000.0)
    s.effective_per_trade_cap = overrides.get("effective_per_trade_cap", 1000.0)
    s.max_daily_orders = overrides.get("max_daily_orders", 20)
    s.allowed_hours_only = overrides.get("allowed_hours_only", False)
    s.blocked_tickers = overrides.get("blocked_tickers", [])
    return s


def test_full_round_trip_produces_gate_evidence():
    db = _make_db()
    db.add(Stock(ticker="NVDA"))
    db.commit()
    db.add(Recommendation(
        ticker="NVDA", date=date.today(), direction="long", strategy="long",
        score=0.9, entry_price=90.0, stop_loss=85.0, target_price=100.0,
        position_size=900.0, max_loss=50.0,
    ))
    db.commit()

    broker = FakeBroker()
    with patch("src.services.execution_engine.get_settings", return_value=_settings()), \
         patch("src.services.safety_rails.get_settings", return_value=_settings()):
        engine = ExecutionEngine(db, alpaca=broker, mapper=OrderMapper())
        results = engine.execute_recommendations()

    assert results and results[0]["status"] == "submitted"
    pt = db.query(PaperTrade).filter_by(ticker="NVDA").one()
    assert pt.status == "open"

    # Broker filled; price later breaches the stop -> paper_exits closes it.
    db.add(PriceHistory(ticker="NVDA", date=date.today(), close=84.0))
    db.commit()
    evaluate_paper_exits(db, today=date.today())

    pt = db.query(PaperTrade).filter_by(ticker="NVDA").one()
    assert pt.status == "closed"
    assert pt.pnl is not None and pt.pnl < 0   # closed below entry

    spec = ArmSpec(name="bull_stock", strategies=("long", "call_options"),
                   baseline=date.today() - timedelta(days=30))
    res = evaluate_arm(db, spec, today=date.today())
    assert res.closed_trades == 1


# ---------------------------------------------------------------------------
# 5. A spread is ONE position against the cap, even though it shows as N
#    option legs at the broker (double-count tripped the cap prematurely).
# ---------------------------------------------------------------------------
def test_spread_counts_as_one_position_not_n_legs():
    db = _make_db()
    _seed_price(db, "AMD", 560.0)
    db.add(PaperTrade(
        ticker="AMD", strategy="bull_spread", status="open", entry_price=7.94,
        position_size=794.0, legs_json=json.dumps(_bull_spread_legs()),
        contracts=1, expiry=date(2026, 8, 7),
    ))
    db.commit()

    # Broker reports the spread as two separate option-leg positions.
    broker = FakeBroker(positions=[
        _position("AMD260807C00530000", 1.0, price=30.0, mkt=3000.0),
        _position("AMD260807C00555000", -1.0, price=5.0, mkt=-500.0),
    ])
    PortfolioSync(db, client=broker).sync_positions()
    assert db.query(AlpacaPosition).count() == 2   # two legs at the broker

    with patch("src.services.safety_rails.get_settings",
               return_value=_settings(max_open_positions=5)):
        rails = TradingSafetyRails(db)
        allowed, _ = rails._check_position_limit()

    assert allowed                                 # 1 PaperTrade, not 3
    open_count = db.query(PaperTrade).filter_by(status="open").count()
    assert open_count == 1


# ---------------------------------------------------------------------------
# 6. An option position that closes (exercise/assignment) leaves an unowned
#    stock position -> flagged as residue against the capital cap.
# ---------------------------------------------------------------------------
@patch("src.services.portfolio_sync.httpx")
def test_exercised_option_leaves_flagged_residue(mock_httpx):
    db = _make_db()
    db.add(Stock(ticker="DOCU"))
    # The option trade has already closed (exercised into stock).
    db.add(PaperTrade(
        ticker="DOCU", strategy="call_options", status="closed",
        entry_price=2.5, closed_at=T0,
    ))
    db.commit()

    # Broker still holds the exercised shares, owned by no open strategy.
    broker = FakeBroker(positions=[_position("DOCU", 300.0, price=49.0, mkt=14700.0)])
    sync = PortfolioSync(db, client=broker)
    sync.sync_positions()
    residue = sync.detect_residue_positions(now=T0)

    assert [r["ticker"] for r in residue] == ["DOCU"]
    assert mock_httpx.post.called                  # operator alerted
