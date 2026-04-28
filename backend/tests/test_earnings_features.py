"""Tests for earnings-proximity features (P9-005)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, EarningsCalendar, Stock
from src.features.earnings import (
    DAYS_TO_CAP,
    DEFAULT_EARNINGS_FEATURES,
    EARNINGS_FEATURE_COLS,
    attach_earnings_features,
    get_earnings_features,
)
from src.pipeline.earnings_fetcher import EarningsFetcher, _parse_calendar


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add(Stock(ticker="AAPL"))
    s.commit()
    yield s
    s.close()


def test_no_earnings_returns_default(db):
    feats = get_earnings_features(db, "AAPL", on_date=date(2026, 4, 25))
    assert feats == DEFAULT_EARNINGS_FEATURES


def test_within_3d(db):
    db.add(EarningsCalendar(ticker="AAPL", earnings_date=date(2026, 4, 27)))
    db.commit()
    feats = get_earnings_features(db, "AAPL", on_date=date(2026, 4, 25))
    assert feats["days_to_earnings"] == 2.0
    assert feats["earnings_within_3d"] == 1.0
    assert feats["earnings_within_10d"] == 1.0


def test_boundary_at_10d(db):
    db.add(EarningsCalendar(ticker="AAPL", earnings_date=date(2026, 5, 5)))
    db.commit()
    feats = get_earnings_features(db, "AAPL", on_date=date(2026, 4, 25))
    assert feats["days_to_earnings"] == 10.0
    assert feats["earnings_within_3d"] == 0.0
    assert feats["earnings_within_10d"] == 1.0  # boundary inclusive


def test_distant_capped(db):
    db.add(EarningsCalendar(ticker="AAPL", earnings_date=date(2026, 4, 25) + timedelta(days=200)))
    db.commit()
    feats = get_earnings_features(db, "AAPL", on_date=date(2026, 4, 25))
    assert feats["days_to_earnings"] == DAYS_TO_CAP


def test_days_since_past_earnings(db):
    db.add(EarningsCalendar(ticker="AAPL", earnings_date=date(2026, 4, 20)))
    db.commit()
    feats = get_earnings_features(db, "AAPL", on_date=date(2026, 4, 25))
    assert feats["days_to_earnings"] == -1.0
    assert feats["days_since_earnings"] == 5.0


def test_attach_join(db):
    db.add(EarningsCalendar(ticker="AAPL", earnings_date=date(2026, 4, 27)))
    db.commit()
    df = pd.DataFrame([{"ticker": "AAPL", "date": date(2026, 4, 25)}])
    out = attach_earnings_features(db, df)
    for col in EARNINGS_FEATURE_COLS:
        assert col in out.columns
    assert out.iloc[0]["earnings_within_3d"] == 1.0


# --- _parse_calendar ---------------------------------------------------------

def test_parse_calendar_dict():
    raw = {"Earnings Date": [datetime(2026, 5, 1), datetime(2026, 8, 1)]}
    out = _parse_calendar(raw)
    assert out == [date(2026, 5, 1), date(2026, 8, 1)]


def test_parse_calendar_none():
    assert _parse_calendar(None) == []


def test_parse_calendar_unrecognized():
    assert _parse_calendar(42) == []


# --- EarningsFetcher ---------------------------------------------------------

class TestEarningsFetcher:
    def test_no_data(self, db):
        fetcher = EarningsFetcher(db=db, sleep_s=0)
        with patch("src.pipeline.earnings_fetcher.yf.Ticker") as mt:
            mt.return_value.calendar = {}
            assert fetcher.fetch_one("AAPL") == "no_data"

    def test_ok_path(self, db):
        fetcher = EarningsFetcher(db=db, sleep_s=0)
        with patch("src.pipeline.earnings_fetcher.yf.Ticker") as mt:
            mt.return_value.calendar = {"Earnings Date": [datetime(2026, 5, 1)]}
            assert fetcher.fetch_one("AAPL") == "ok"

        rows = db.query(EarningsCalendar).filter_by(ticker="AAPL").all()
        assert len(rows) == 1
        assert rows[0].earnings_date == date(2026, 5, 1)

    def test_idempotent(self, db):
        fetcher = EarningsFetcher(db=db, sleep_s=0)
        with patch("src.pipeline.earnings_fetcher.yf.Ticker") as mt:
            mt.return_value.calendar = {"Earnings Date": [datetime(2026, 5, 1)]}
            fetcher.fetch_one("AAPL")
            fetcher.fetch_one("AAPL")
        assert db.query(EarningsCalendar).filter_by(ticker="AAPL").count() == 1
