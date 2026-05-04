"""Tests for short interest features (P10-003)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, ShortInterestSnapshot, Stock
from src.features.short_interest import (
    DAYS_SINCE_CAP,
    DEFAULT_SHORT_INTEREST_FEATURES,
    SHORT_INTEREST_FEATURE_COLS,
    attach_short_interest_features,
    get_short_interest_features,
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


def _add(db, ticker, d, shares_short=10_000_000.0, sp_float=0.05,
         days_to_cover=2.5, has_data=1):
    db.add(ShortInterestSnapshot(
        ticker=ticker,
        report_date=d,
        shares_short=shares_short,
        short_percent_of_float=sp_float,
        short_ratio_days_to_cover=days_to_cover,
        has_data=has_data,
        fetched_at=datetime.utcnow(),
    ))


def test_no_snapshots_returns_default(db):
    feats = get_short_interest_features(db, "AAPL", on_date=date(2026, 4, 25))
    assert feats == DEFAULT_SHORT_INTEREST_FEATURES


def test_most_recent_snapshot_surfaces(db):
    today = date(2026, 4, 25)
    _add(db, "AAPL", today - timedelta(days=10),
         shares_short=10_000_000.0, sp_float=0.05, days_to_cover=2.5)
    db.commit()
    f = get_short_interest_features(db, "AAPL", on_date=today)
    assert f["short_percent_of_float"] == 0.05
    assert f["short_ratio_days_to_cover"] == 2.5
    assert f["days_since_short_report"] == 10.0
    assert f["has_short_data"] == 1.0


def test_change_pct_between_two_snapshots(db):
    today = date(2026, 4, 25)
    _add(db, "AAPL", today - timedelta(days=30), shares_short=10_000_000.0)
    _add(db, "AAPL", today - timedelta(days=15), shares_short=12_000_000.0)
    db.commit()
    f = get_short_interest_features(db, "AAPL", on_date=today)
    assert f["short_interest_change_pct"] == pytest.approx(0.20)


def test_zscore_requires_three_snapshots(db):
    today = date(2026, 4, 25)
    # Two snapshots — z-score should be 0 (insufficient data)
    _add(db, "AAPL", today - timedelta(days=30), shares_short=10_000_000.0)
    _add(db, "AAPL", today - timedelta(days=15), shares_short=11_000_000.0)
    db.commit()
    f = get_short_interest_features(db, "AAPL", on_date=today)
    assert f["short_interest_zscore_180d"] == 0.0


def test_zscore_computed_over_window(db):
    today = date(2026, 4, 25)
    # Three snapshots inside window: 10M, 10M, 14M → mean=11.33, current=14M → positive z
    _add(db, "AAPL", today - timedelta(days=60), shares_short=10_000_000.0)
    _add(db, "AAPL", today - timedelta(days=30), shares_short=10_000_000.0)
    _add(db, "AAPL", today - timedelta(days=5), shares_short=14_000_000.0)
    db.commit()
    f = get_short_interest_features(db, "AAPL", on_date=today)
    assert f["short_interest_zscore_180d"] > 1.0


def test_days_since_capped(db):
    today = date(2026, 4, 25)
    _add(db, "AAPL", today - timedelta(days=500))
    db.commit()
    f = get_short_interest_features(db, "AAPL", on_date=today)
    assert f["days_since_short_report"] == DAYS_SINCE_CAP


def test_future_snapshots_ignored(db):
    today = date(2026, 4, 25)
    _add(db, "AAPL", today + timedelta(days=5))
    db.commit()
    f = get_short_interest_features(db, "AAPL", on_date=today)
    assert f == DEFAULT_SHORT_INTEREST_FEATURES


def test_has_data_zero_excluded(db):
    today = date(2026, 4, 25)
    # Stub row with has_data=0 should be filtered out
    _add(db, "AAPL", today - timedelta(days=5), has_data=0)
    db.commit()
    f = get_short_interest_features(db, "AAPL", on_date=today)
    assert f == DEFAULT_SHORT_INTEREST_FEATURES


def test_attach_join_per_ticker(db):
    today = date(2026, 4, 25)
    _add(db, "AAPL", today - timedelta(days=10),
         shares_short=10_000_000.0, sp_float=0.06)
    _add(db, "MSFT", today - timedelta(days=20),
         shares_short=5_000_000.0, sp_float=0.02)
    db.commit()
    df = pd.DataFrame([
        {"ticker": "AAPL", "date": today},
        {"ticker": "MSFT", "date": today},
    ])
    out = attach_short_interest_features(db, df)
    for col in SHORT_INTEREST_FEATURE_COLS:
        assert col in out.columns
    aapl = out[out["ticker"] == "AAPL"].iloc[0]
    msft = out[out["ticker"] == "MSFT"].iloc[0]
    assert aapl["short_percent_of_float"] == 0.06
    assert aapl["days_since_short_report"] == 10.0
    assert msft["short_percent_of_float"] == 0.02
    assert msft["days_since_short_report"] == 20.0


def test_attach_empty_df_adds_default_columns(db):
    df = pd.DataFrame(columns=["ticker", "date"])
    out = attach_short_interest_features(db, df)
    for col, default in DEFAULT_SHORT_INTEREST_FEATURES.items():
        assert col in out.columns
