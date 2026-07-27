"""Exit evaluation for open paper trades (audit item 5, 2026-07-02).

PaperTrade rows were created at submission but nothing ever closed them —
4 trades sat open from 2026-06-09 past their option expiries. These tests
lock the exit rules: stock stop/target crosses, single-leg intrinsic at
expiry, and spread P&L reconstructed from legs_json premiums.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, PaperTrade, PriceHistory, Stock
from src.services.paper_exits import evaluate_paper_exits

TODAY = date(2026, 7, 2)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _seed_price(db, ticker: str, close: float, d: date = TODAY):
    db.add(Stock(ticker=ticker))
    db.add(PriceHistory(ticker=ticker, date=d, close=close))
    db.commit()


def _trade(**kw) -> PaperTrade:
    defaults = dict(
        ticker="TEST", direction="long", strategy="long", status="open",
        entry_price=100.0, opened_at=datetime(2026, 6, 9),
    )
    defaults.update(kw)
    return PaperTrade(**defaults)


def test_long_stock_stop_hit(db):
    _seed_price(db, "TEST", 89.0)
    db.add(_trade(strategy="long", entry_price=100.0, stop_loss=90.0,
                  target_price=120.0, position_size=1000.0))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    assert results[0]["status"] == "closed"
    t = db.query(PaperTrade).one()
    assert t.status == "closed"
    # 10 shares * (89 - 100) = -110
    assert t.pnl == pytest.approx(-110.0)


def test_short_stock_target_hit(db):
    _seed_price(db, "TEST", 80.0)
    # size_short persists position_size as MARGIN (1.5x notional):
    # 10 shares @ 100 -> notional 1000, margin 1500.
    db.add(_trade(strategy="short", direction="short", entry_price=100.0,
                  stop_loss=110.0, target_price=85.0, position_size=1500.0))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    assert results[0]["status"] == "closed"
    t = db.query(PaperTrade).one()
    # 10 shares * (100 - 80) = +200
    assert t.pnl == pytest.approx(200.0)


def test_stock_in_band_stays_open(db):
    _seed_price(db, "TEST", 100.0)
    db.add(_trade(strategy="long", entry_price=100.0, stop_loss=90.0,
                  target_price=120.0, position_size=1000.0))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    assert results[0]["status"] == "held"
    assert db.query(PaperTrade).one().status == "open"


def test_put_option_expires_in_the_money(db):
    _seed_price(db, "TEST", 90.0)
    db.add(_trade(strategy="options", direction="short", strike=100.0,
                  option_type="put", contracts=2, position_size=800.0,
                  expiry=TODAY - timedelta(days=1)))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    assert results[0]["status"] == "closed"
    t = db.query(PaperTrade).one()
    # intrinsic 10 * 100 * 2 = 2000, minus 800 premium = +1200
    assert t.pnl == pytest.approx(1200.0)


def test_call_option_expires_worthless(db):
    _seed_price(db, "TEST", 90.0)
    db.add(_trade(strategy="call_options", strike=100.0, option_type="call",
                  contracts=1, position_size=300.0,
                  expiry=TODAY - timedelta(days=1)))
    db.commit()

    evaluate_paper_exits(db, today=TODAY)

    t = db.query(PaperTrade).one()
    assert t.status == "closed"
    assert t.pnl == pytest.approx(-300.0)


def test_option_before_expiry_stays_open(db):
    _seed_price(db, "TEST", 90.0)
    db.add(_trade(strategy="options", strike=100.0, option_type="put",
                  contracts=1, position_size=300.0,
                  expiry=TODAY + timedelta(days=7)))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    assert results[0]["status"] == "held"


def test_debit_put_spread_expiry_pnl(db):
    """Bear put debit spread: buy 390P @18.53, sell 370P @10.83 (real 6/09 legs).
    Underlying at 360 -> both ITM: (30 - 10) * 100 = 2000 payout,
    net debit (18.53 - 10.83) * 100 = 770. P&L = +1230."""
    _seed_price(db, "TEST", 360.0)
    legs = json.dumps([
        {"option_type": "put", "action": "buy", "strike": 390.0, "premium": 18.53, "contracts": 1},
        {"option_type": "put", "action": "sell", "strike": 370.0, "premium": 10.83, "contracts": 1},
    ])
    db.add(_trade(strategy="spread", direction="short", legs_json=legs,
                  contracts=1, position_size=770.0,
                  expiry=TODAY - timedelta(days=1)))
    db.commit()

    evaluate_paper_exits(db, today=TODAY)

    t = db.query(PaperTrade).one()
    assert t.status == "closed"
    assert t.pnl == pytest.approx(1230.0)


def test_credit_call_spread_expires_worthless_keeps_credit(db):
    """Bear call credit spread: sell 110C @3.00, buy 120C @1.00. Underlying 100
    at expiry -> both worthless, keep net credit (3-1)*100 = +200."""
    _seed_price(db, "TEST", 100.0)
    legs = json.dumps([
        {"option_type": "call", "action": "sell", "strike": 110.0, "premium": 3.00, "contracts": 1},
        {"option_type": "call", "action": "buy", "strike": 120.0, "premium": 1.00, "contracts": 1},
    ])
    db.add(_trade(strategy="spread", direction="short", legs_json=legs,
                  contracts=1, max_loss=800.0,
                  expiry=TODAY - timedelta(days=1)))
    db.commit()

    evaluate_paper_exits(db, today=TODAY)

    t = db.query(PaperTrade).one()
    assert t.status == "closed"
    assert t.pnl == pytest.approx(200.0)


def test_bull_spread_catastrophic_underlying_drop(db):
    """bull_spread (direction long) — underlying drops 17% (> 15% threshold)
    well before expiry -> catastrophic early exit instead of riding to expiry."""
    _seed_price(db, "TEST", 83.0)  # entry 100 -> -17%
    legs = json.dumps([
        {"option_type": "put", "action": "sell", "strike": 100.0, "premium": 3.00, "contracts": 1},
        {"option_type": "put", "action": "buy", "strike": 95.0, "premium": 1.00, "contracts": 1},
    ])
    db.add(_trade(strategy="bull_spread", direction="long", entry_price=100.0,
                  legs_json=legs, contracts=1, position_size=200.0, max_loss=300.0,
                  expiry=TODAY + timedelta(days=30)))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    assert results[0]["status"] == "closed"
    assert "catastrophic" in results[0]["reason"]
    assert db.query(PaperTrade).one().status == "closed"


def test_bear_option_catastrophic_underlying_rise(db):
    """options put (direction short) — underlying rises 17% before expiry -> exit."""
    _seed_price(db, "TEST", 117.0)  # entry 100 -> +17%
    db.add(_trade(strategy="options", direction="short", entry_price=100.0,
                  strike=100.0, option_type="put", contracts=1, position_size=800.0,
                  expiry=TODAY + timedelta(days=30)))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    assert results[0]["status"] == "closed"
    assert "catastrophic" in results[0]["reason"]


def test_option_moderate_move_holds_to_expiry(db):
    """A 10% adverse move (< 15% threshold) does not trigger catastrophic exit."""
    _seed_price(db, "TEST", 110.0)  # entry 100 -> +10%, under threshold
    db.add(_trade(strategy="options", direction="short", entry_price=100.0,
                  strike=100.0, option_type="put", contracts=1, position_size=800.0,
                  expiry=TODAY + timedelta(days=30)))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    assert results[0]["status"] == "held"
    assert db.query(PaperTrade).one().status == "open"


def test_no_price_data_skips(db):
    db.add(Stock(ticker="TEST"))
    db.add(_trade(strategy="long", stop_loss=90.0, position_size=1000.0))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    assert results[0]["status"] == "skipped"
    assert db.query(PaperTrade).one().status == "open"


def test_spread_without_expiry_stays_open(db):
    """Legacy rows (pre-migration) have expiry=None — must not close or crash."""
    _seed_price(db, "TEST", 100.0)
    db.add(_trade(strategy="spread", legs_json="[]", expiry=None))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    assert results[0]["status"] == "held"


def _seed_sessions(db, ticker: str, closes: list[float], start: date):
    """Seed consecutive daily price rows; last close = current price."""
    db.add(Stock(ticker=ticker))
    for i, c in enumerate(closes):
        db.add(PriceHistory(ticker=ticker, date=start + timedelta(days=i), close=c))
    db.commit()


def test_long_time_exit_after_10_sessions(db):
    """In-band long must close on the time exit once 10 sessions elapse."""
    _seed_sessions(db, "TEST", [100.0] * 11, date(2026, 6, 20))  # 10 rows after open date
    db.add(_trade(strategy="long", entry_price=100.0, stop_loss=90.0,
                  target_price=120.0, position_size=1000.0,
                  opened_at=datetime(2026, 6, 20)))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    assert results[0]["status"] == "closed"
    t = db.query(PaperTrade).one()
    assert t.pnl == pytest.approx(0.0)


def test_long_before_time_exit_stays_open(db):
    _seed_sessions(db, "TEST", [100.0] * 4, date(2026, 6, 28))  # 3 sessions after open
    db.add(_trade(strategy="long", entry_price=100.0, stop_loss=90.0,
                  target_price=120.0, position_size=1000.0,
                  opened_at=datetime(2026, 6, 28)))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    assert results[0]["status"] == "held"


def test_short_share_count_backs_out_margin(db):
    """size_short persists position_size as 1.5x margin — pnl must use real
    shares (notional/entry), not margin/entry."""
    _seed_price(db, "TEST", 90.0)
    # 10 shares at $100 -> notional 1000, margin 1500
    db.add(_trade(strategy="short", direction="short", entry_price=100.0,
                  stop_loss=110.0, target_price=92.0, position_size=1500.0))
    db.commit()

    evaluate_paper_exits(db, today=TODAY)

    t = db.query(PaperTrade).one()
    # target hit: 10 shares * (100 - 90) = +100, NOT 15 shares * 10 = 150
    assert t.pnl == pytest.approx(100.0)


def _pair_legs(short_qty=10, short_entry=100.0, hedge_qty=2, hedge_entry=500.0):
    return json.dumps([
        {"leg": "short", "ticker": "TEST", "qty": short_qty, "entry": short_entry},
        {"leg": "hedge", "ticker": "SPY", "qty": hedge_qty, "entry": hedge_entry},
    ])


def test_pair_short_time_exit_pnl_sums_both_legs(db):
    """Pick fell 100->95 (+50 on 10 short shares), SPY rose 500->510
    (+20 on 2 hedge shares) -> pnl +70 at the 10-session time exit."""
    _seed_sessions(db, "TEST", [95.0] * 11, date(2026, 6, 20))
    _seed_sessions(db, "SPY", [510.0] * 11, date(2026, 6, 20))
    db.add(_trade(strategy="pair_short", direction="short", entry_price=100.0,
                  stop_loss=105.0, target_price=90.0, position_size=2500.0,
                  legs_json=_pair_legs(), opened_at=datetime(2026, 6, 20)))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    closed = [r for r in results if r.get("ticker") == "TEST"]
    assert closed[0]["status"] == "closed"
    t = db.query(PaperTrade).filter_by(ticker="TEST").one()
    assert t.pnl == pytest.approx(70.0)


def test_pair_short_stop_exit_before_time(db):
    """Short-leg stop breach closes the pair immediately, hedge pnl included."""
    _seed_sessions(db, "TEST", [106.0] * 3, date(2026, 6, 29))  # above 105 stop
    _seed_sessions(db, "SPY", [505.0] * 3, date(2026, 6, 29))
    db.add(_trade(strategy="pair_short", direction="short", entry_price=100.0,
                  stop_loss=105.0, target_price=90.0, position_size=2500.0,
                  legs_json=_pair_legs(), opened_at=datetime(2026, 6, 29)))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    closed = [r for r in results if r.get("ticker") == "TEST"]
    assert closed[0]["status"] == "closed"
    t = db.query(PaperTrade).filter_by(ticker="TEST").one()
    # short: 10 * (100 - 106) = -60; hedge: 2 * (505 - 500) = +10
    assert t.pnl == pytest.approx(-50.0)


def test_pair_short_in_band_stays_open(db):
    _seed_sessions(db, "TEST", [100.0] * 3, date(2026, 6, 29))
    _seed_sessions(db, "SPY", [500.0] * 3, date(2026, 6, 29))
    db.add(_trade(strategy="pair_short", direction="short", entry_price=100.0,
                  stop_loss=105.0, target_price=90.0, position_size=2500.0,
                  legs_json=_pair_legs(), opened_at=datetime(2026, 6, 29)))
    db.commit()

    results = evaluate_paper_exits(db, today=TODAY)

    closed = [r for r in results if r.get("ticker") == "TEST"]
    assert closed[0]["status"] == "held"
