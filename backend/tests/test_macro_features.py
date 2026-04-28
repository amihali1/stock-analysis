"""Tests for macro/regime feature extraction (P9-002)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, PriceHistory, Stock
from src.features.macro import (
    DEFAULT_MACRO_FEATURES,
    MACRO_FEATURE_COLS,
    attach_macro_features,
    get_macro_features,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add(Stock(ticker="SPY"))
    s.add(Stock(ticker="^VIX"))
    s.commit()
    yield s
    s.close()


def _seed(db, ticker: str, start: date, closes: list[float]):
    for i, c in enumerate(closes):
        db.add(PriceHistory(
            ticker=ticker, date=start + timedelta(days=i),
            open=c, high=c, low=c, close=c, volume=1_000_000,
        ))
    db.commit()


def test_no_data_returns_defaults(db):
    feats = get_macro_features(db)
    assert feats == DEFAULT_MACRO_FEATURES


def test_basic_macro(db):
    start = date(2025, 1, 1)
    spy_closes = [400.0 + i * 0.5 for i in range(220)]  # rising trend
    vix_closes = [15.0 + (i % 10) for i in range(220)]
    _seed(db, "SPY", start, spy_closes)
    _seed(db, "^VIX", start, vix_closes)

    feats = get_macro_features(db, on_date=start + timedelta(days=219))
    # Trending SPY → above both SMAs and at 252d high → drawdown == 0
    assert feats["spy_above_sma_50"] == 1.0
    assert feats["spy_above_sma_200"] == 1.0
    assert feats["spy_drawdown_pct"] == pytest.approx(0.0, abs=1e-9)
    # VIX level present
    assert feats["vix_level"] > 0
    # Returns nonzero (rising)
    assert feats["spy_return_5d"] > 0
    assert feats["spy_return_20d"] > 0


def test_drawdown_negative_after_peak(db):
    start = date(2025, 1, 1)
    closes = [400.0 + i for i in range(100)] + [499.0 - i for i in range(50)]
    _seed(db, "SPY", start, closes)
    feats = get_macro_features(db, on_date=start + timedelta(days=149))
    # Peak ~499, current ~449 → drawdown roughly -10%
    assert feats["spy_drawdown_pct"] < -0.05


def test_attach_join_falls_back_for_missing_dates(db):
    start = date(2025, 1, 1)
    _seed(db, "SPY", start, [400.0 + i for i in range(40)])
    # No VIX seeded — VIX values should ffill to NaN, then defaults

    df = pd.DataFrame([
        {"ticker": "AAPL", "date": start - timedelta(days=10)},  # before any SPY data
        {"ticker": "AAPL", "date": start + timedelta(days=30)},  # within range
    ])
    out = attach_macro_features(db, df)
    for col in MACRO_FEATURE_COLS:
        assert col in out.columns

    # First row (before SPY) gets defaults; second has real macro values
    early = out.iloc[0]
    later = out.iloc[1]
    assert early["spy_return_5d"] == DEFAULT_MACRO_FEATURES["spy_return_5d"]
    assert later["vix_level"] == DEFAULT_MACRO_FEATURES["vix_level"]  # no VIX data → default


def test_vix_forward_fill_only(db):
    start = date(2025, 1, 1)
    _seed(db, "SPY", start, [400.0] * 30)
    # VIX has a gap in the middle
    db.add(PriceHistory(ticker="^VIX", date=start, close=15.0, open=15, high=15, low=15, volume=0))
    db.add(PriceHistory(ticker="^VIX", date=start + timedelta(days=10), close=25.0, open=25, high=25, low=25, volume=0))
    db.commit()

    feats_day_5 = get_macro_features(db, on_date=start + timedelta(days=5))
    # Day 5 has SPY but no VIX yet on that day; ffill from day 0 → 15.0
    assert feats_day_5["vix_level"] == pytest.approx(15.0)
