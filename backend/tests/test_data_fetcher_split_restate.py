"""Stored price history must be restated when yfinance re-bases it.

Regression for 2026-09-29: price_history rows were only ever inserted, never
rewritten, so KLAC's 10:1 split (2026-06-12) left ~2100 pre-split closes next
to ~210 post-split ones. volatility_20d hit 32.5 and poisoned features and
backtests. HON's 2026-06-29 spin-off adjustment had the same shape.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, PriceHistory, Stock, TechnicalIndicator
from src.pipeline.data_fetcher import DataFetcher


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


DAYS = pd.bdate_range("2026-06-01", "2026-06-12")  # 10 sessions


def _df(dates, closes) -> pd.DataFrame:
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes,
         "Volume": [1e6] * len(closes), "Adj Close": closes},
        index=pd.DatetimeIndex(dates),
    )


def _seed(db, ticker, dates, closes, with_indicators=True):
    db.add(Stock(ticker=ticker, name=ticker))
    for d, c in zip(dates, closes):
        db.add(PriceHistory(ticker=ticker, date=d.date(), close=c))
        if with_indicators:
            db.add(TechnicalIndicator(ticker=ticker, date=d.date()))
    db.commit()


def _mock_ticker(window_df, full_df=None):
    """history(period=...) → the fetch window; history(start=...) → full/overlap refetch."""
    t = MagicMock()
    t.info = {}

    def history(**kw):
        if "start" in kw:
            start = pd.Timestamp(kw["start"])
            src = full_df if full_df is not None else window_df
            return src[src.index >= start]
        return window_df

    t.history.side_effect = history
    return t


def _closes(db, ticker):
    return {r.date: r.close for r in db.query(PriceHistory).filter_by(ticker=ticker)}


def test_split_in_overlap_restates_full_history(db_session):
    # Stored: 8 unadjusted pre-split sessions at ~2000.
    _seed(db_session, "KLAC", DAYS[:8], [2000.0 + i for i in range(8)])
    # yfinance now serves everything split-adjusted (/10), plus 2 new sessions.
    adjusted = _df(DAYS, [200.0 + i / 10 for i in range(10)])
    fetcher = DataFetcher(db_session)

    with patch("src.pipeline.data_fetcher.yf.Ticker", return_value=_mock_ticker(adjusted[-5:], adjusted)):
        inserted = fetcher._fetch_ticker("KLAC", period="5d")

    assert inserted == 10
    closes = _closes(db_session, "KLAC")
    assert len(closes) == 10
    assert all(190 < c < 210 for c in closes.values())
    assert db_session.query(TechnicalIndicator).filter_by(ticker="KLAC").count() == 0
    assert fetcher.restated == ["KLAC"]


def test_consistent_history_only_inserts_new_rows(db_session):
    closes = [100.0 + i for i in range(10)]
    _seed(db_session, "AAPL", DAYS[:8], closes[:8])
    fetcher = DataFetcher(db_session)

    with patch("src.pipeline.data_fetcher.yf.Ticker", return_value=_mock_ticker(_df(DAYS[-5:], closes[-5:]))):
        inserted = fetcher._fetch_ticker("AAPL", period="5d")

    assert inserted == 2
    assert len(_closes(db_session, "AAPL")) == 10
    assert db_session.query(TechnicalIndicator).filter_by(ticker="AAPL").count() == 8
    assert fetcher.restated == []


def test_small_revision_below_tolerance_not_restated(db_session):
    _seed(db_session, "MSFT", DAYS[:8], [100.0] * 8)
    revised = _df(DAYS[-5:], [100.5] * 5)  # 0.5% vendor revision
    fetcher = DataFetcher(db_session)

    with patch("src.pipeline.data_fetcher.yf.Ticker", return_value=_mock_ticker(revised)):
        fetcher._fetch_ticker("MSFT", period="5d")

    assert fetcher.restated == []
    assert _closes(db_session, "MSFT")[DAYS[0].date()] == 100.0


def test_spinoff_sized_adjustment_detected(db_session):
    """HON 2026-06-29: factor 0.9535 (~4.7%) must trip the check."""
    _seed(db_session, "HON", DAYS[:8], [240.0] * 8)
    adjusted = _df(DAYS, [240.0 * 0.9535] * 10)
    fetcher = DataFetcher(db_session)

    with patch("src.pipeline.data_fetcher.yf.Ticker", return_value=_mock_ticker(adjusted[-5:], adjusted)):
        fetcher._fetch_ticker("HON", period="5d")

    assert fetcher.restated == ["HON"]


def test_gap_longer_than_window_still_detects_split(db_session):
    """Outage: the 5d window shares no dates with stored rows, so the fetcher
    must reach back to the last stored date to compare."""
    all_days = pd.bdate_range("2026-06-01", "2026-06-26")  # 20 sessions
    _seed(db_session, "KLAC", all_days[:5], [2000.0] * 5)
    adjusted = _df(all_days, [200.0] * 20)
    fetcher = DataFetcher(db_session)

    with patch("src.pipeline.data_fetcher.yf.Ticker", return_value=_mock_ticker(adjusted[-5:], adjusted)):
        fetcher._fetch_ticker("KLAC", period="5d")

    assert fetcher.restated == ["KLAC"]
    assert all(c == 200.0 for c in _closes(db_session, "KLAC").values())
    assert len(_closes(db_session, "KLAC")) == 20


def test_empty_full_refetch_keeps_existing_rows(db_session):
    _seed(db_session, "KLAC", DAYS[:8], [2000.0] * 8)
    window = _df(DAYS[-5:], [200.0] * 5)
    fetcher = DataFetcher(db_session)
    t = _mock_ticker(window)
    t.history.side_effect = lambda **kw: pd.DataFrame() if "start" in kw else window

    with patch("src.pipeline.data_fetcher.yf.Ticker", return_value=t):
        assert fetcher._fetch_ticker("KLAC", period="5d") == 0

    assert len(_closes(db_session, "KLAC")) == 8
    assert fetcher.restated == []


def test_new_ticker_without_history_just_inserts(db_session):
    fetcher = DataFetcher(db_session)
    with patch("src.pipeline.data_fetcher.yf.Ticker", return_value=_mock_ticker(_df(DAYS, [50.0] * 10))):
        assert fetcher._fetch_ticker("NEW", period="2y") == 10
    assert fetcher.restated == []
