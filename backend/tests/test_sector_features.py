"""Tests for sector relative-strength features (P9-003)."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config import sector_etf_for
from src.db.models import Base, PriceHistory, Stock
from src.features.sector import (
    DEFAULT_SECTOR_FEATURES,
    SECTOR_FEATURE_COLS,
    attach_sector_features,
    get_sector_features,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    for t in ("AAPL", "FAKE", "XLK", "SPY"):
        s.add(Stock(ticker=t))
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
    feats = get_sector_features(db, "AAPL")
    assert feats == DEFAULT_SECTOR_FEATURES


def test_aapl_maps_to_xlk():
    assert sector_etf_for("AAPL") == "XLK"


def test_unknown_ticker_falls_back_to_spy():
    assert sector_etf_for("NEVER_HEARD_OF") == "SPY"


def test_sector_relative_return(db):
    start = date(2025, 1, 1)
    # AAPL up 5% over 5 days, XLK up 1% over 5 days → relative +4%
    _seed(db, "AAPL", start, [100.0, 100.5, 101.0, 102.0, 103.0, 105.0])
    _seed(db, "XLK", start, [50.0, 50.1, 50.2, 50.3, 50.4, 50.5])

    feats = get_sector_features(db, "AAPL", on_date=start + timedelta(days=5))
    assert feats["sector_return_5d"] == pytest.approx(0.01, abs=1e-3)
    assert feats["return_5d_vs_sector"] > 0  # AAPL outperformed


def test_unknown_ticker_uses_spy(db):
    start = date(2025, 1, 1)
    _seed(db, "FAKE", start, [10.0 + i for i in range(30)])
    _seed(db, "SPY", start, [400.0] * 30)  # SPY flat

    feats = get_sector_features(db, "FAKE", on_date=start + timedelta(days=29))
    # SPY flat → sector_return_5d ≈ 0; FAKE up → relative > 0
    assert feats["sector_return_5d"] == pytest.approx(0.0, abs=1e-9)
    assert feats["return_5d_vs_sector"] > 0


def test_attach_join(db):
    start = date(2025, 1, 1)
    _seed(db, "AAPL", start, [100.0 + i for i in range(30)])
    _seed(db, "XLK", start, [50.0 + i * 0.5 for i in range(30)])

    df = pd.DataFrame([
        {"ticker": "AAPL", "date": start + timedelta(days=20)},
        {"ticker": "AAPL", "date": start + timedelta(days=25)},
    ])
    out = attach_sector_features(db, df)
    for col in SECTOR_FEATURE_COLS:
        assert col in out.columns
    # Both rows should have non-default values now
    assert out.iloc[0]["sector_return_5d"] != 0.0
