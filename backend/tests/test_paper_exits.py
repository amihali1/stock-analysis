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
    db.add(_trade(strategy="short", direction="short", entry_price=100.0,
                  stop_loss=110.0, target_price=85.0, position_size=1000.0))
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
