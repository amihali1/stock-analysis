"""Tests for analyst rating-change features (P10-001)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import AnalystRating, Base, Stock
from src.features.analyst import (
    ANALYST_FEATURE_COLS,
    DAYS_SINCE_CAP,
    DEFAULT_ANALYST_FEATURES,
    attach_analyst_features,
    get_analyst_features,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add(Stock(ticker="AAPL"))
    s.add(Stock(ticker="MSFT"))
    s.commit()
    yield s
    s.close()


def _add(db, ticker, d, action, firm="Goldman", to="Buy"):
    db.add(AnalystRating(
        ticker=ticker, date=d, firm=firm, to_grade=to,
        from_grade="Hold", action=action, source="test",
        fetched_at=datetime.utcnow(),
    ))


def test_no_ratings_returns_default(db):
    feats = get_analyst_features(db, "AAPL", on_date=date(2026, 4, 25))
    assert feats == DEFAULT_ANALYST_FEATURES


def test_recent_downgrade_dominates(db):
    today = date(2026, 4, 25)
    _add(db, "AAPL", today - timedelta(days=2), "down")
    _add(db, "AAPL", today - timedelta(days=10), "up", firm="MS")
    db.commit()
    f = get_analyst_features(db, "AAPL", on_date=today)
    assert f["days_since_downgrade"] == 2.0
    assert f["days_since_upgrade"] == 10.0
    assert f["downgrades_30d"] == 1.0
    assert f["upgrades_30d"] == 1.0
    assert f["net_rating_actions_60d"] == 0.0
    assert f["analyst_action_5d"] == 1.0


def test_window_excludes_old_actions(db):
    today = date(2026, 4, 25)
    _add(db, "AAPL", today - timedelta(days=45), "down")  # outside 30d, inside 60d
    _add(db, "AAPL", today - timedelta(days=100), "up")   # outside 60d
    db.commit()
    f = get_analyst_features(db, "AAPL", on_date=today)
    assert f["downgrades_30d"] == 0.0
    assert f["upgrades_30d"] == 0.0
    assert f["net_rating_actions_60d"] == -1.0
    assert f["analyst_action_5d"] == 0.0


def test_days_since_capped(db):
    today = date(2026, 4, 25)
    _add(db, "AAPL", today - timedelta(days=500), "down")
    db.commit()
    f = get_analyst_features(db, "AAPL", on_date=today)
    assert f["days_since_downgrade"] == DAYS_SINCE_CAP


def test_action_normalization_case_insensitive(db):
    today = date(2026, 4, 25)
    _add(db, "AAPL", today - timedelta(days=1), "Down")  # mixed case
    db.commit()
    f = get_analyst_features(db, "AAPL", on_date=today)
    assert f["downgrades_30d"] == 1.0


def test_future_actions_ignored(db):
    today = date(2026, 4, 25)
    _add(db, "AAPL", today + timedelta(days=2), "down")
    db.commit()
    f = get_analyst_features(db, "AAPL", on_date=today)
    assert f == DEFAULT_ANALYST_FEATURES


def test_attach_join_per_ticker(db):
    today = date(2026, 4, 25)
    _add(db, "AAPL", today - timedelta(days=2), "down")
    _add(db, "MSFT", today - timedelta(days=2), "up")
    db.commit()
    df = pd.DataFrame([
        {"ticker": "AAPL", "date": today},
        {"ticker": "MSFT", "date": today},
    ])
    out = attach_analyst_features(db, df)
    for col in ANALYST_FEATURE_COLS:
        assert col in out.columns
    aapl = out[out["ticker"] == "AAPL"].iloc[0]
    msft = out[out["ticker"] == "MSFT"].iloc[0]
    assert aapl["downgrades_30d"] == 1.0
    assert aapl["upgrades_30d"] == 0.0
    assert msft["upgrades_30d"] == 1.0
    assert msft["downgrades_30d"] == 0.0
