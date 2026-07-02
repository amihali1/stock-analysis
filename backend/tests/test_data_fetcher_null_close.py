"""Ingest must not persist rows with a missing close.

Regression for 2026-07-02: yfinance returned partial-day rows (OHLV present,
close NaN) on 2026-06-09/15/16/29. _safe_float mapped NaN -> None and the row
was inserted anyway, permanently poisoning price_history (the existing_dates
dedup means a poisoned date is never re-fetched). Downstream,
annualized_vol_20d crashed on the None and zeroed recommendations for three
weeks.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, PriceHistory
from src.pipeline.data_fetcher import DataFetcher


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _history_df() -> pd.DataFrame:
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2026-06-08"), pd.Timestamp("2026-06-09"), pd.Timestamp("2026-06-10")]
    )
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, float("nan"), 102.5],
            "Volume": [1_000_000, 1_100_000, 1_200_000],
            "Adj Close": [100.5, float("nan"), 102.5],
        },
        index=idx,
    )


def test_nan_close_row_skipped_not_persisted(db_session):
    fetcher = DataFetcher(db_session)
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _history_df()
    mock_ticker.info = {}

    with patch("src.pipeline.data_fetcher.yf.Ticker", return_value=mock_ticker):
        inserted = fetcher._fetch_ticker("TEST", period="5d")

    assert inserted == 2
    rows = db_session.query(PriceHistory).filter_by(ticker="TEST").all()
    assert {r.date for r in rows} == {date(2026, 6, 8), date(2026, 6, 10)}
    assert all(r.close is not None for r in rows)


def test_skipped_date_refetched_when_complete(db_session):
    """A NaN-close date must remain fetchable: once Yahoo serves the complete
    row, a later fetch inserts it (the whole point of skipping vs persisting)."""
    fetcher = DataFetcher(db_session)
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _history_df()
    mock_ticker.info = {}

    with patch("src.pipeline.data_fetcher.yf.Ticker", return_value=mock_ticker):
        fetcher._fetch_ticker("TEST", period="5d")

    fixed = _history_df()
    fixed.loc[pd.Timestamp("2026-06-09"), "Close"] = 101.5
    fixed.loc[pd.Timestamp("2026-06-09"), "Adj Close"] = 101.5
    mock_ticker.history.return_value = fixed

    with patch("src.pipeline.data_fetcher.yf.Ticker", return_value=mock_ticker):
        inserted = fetcher._fetch_ticker("TEST", period="5d")

    assert inserted == 1
    row = (
        db_session.query(PriceHistory)
        .filter_by(ticker="TEST", date=date(2026, 6, 9))
        .one()
    )
    assert row.close == 101.5
