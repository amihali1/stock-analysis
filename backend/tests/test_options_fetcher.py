"""Tests for the daily options-snapshot fetcher."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, OptionsSnapshot, PriceHistory, Stock
from src.pipeline.options_fetcher import (
    OptionsFetcher,
    _atm_iv,
    _nearest_expiration,
    _skew_iv,
)


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


def _seed_price(db, ticker: str, close: float, day: date | None = None):
    db.add(PriceHistory(
        ticker=ticker,
        date=day or date.today(),
        open=close, high=close, low=close, close=close, volume=1_000_000,
    ))
    db.commit()


def _chain_df(strikes: list[float], ivs: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"strike": strikes, "impliedVolatility": ivs})


class TestNearestExpiration:
    def test_picks_first_at_or_after_target(self):
        today = date(2026, 4, 1)
        exps = [
            (today + timedelta(days=10)).isoformat(),
            (today + timedelta(days=35)).isoformat(),
            (today + timedelta(days=60)).isoformat(),
        ]
        # target = 30 → 35-day exp is the first ≥ 30
        assert _nearest_expiration(exps, today, 30) == exps[1]

    def test_none_when_empty(self):
        assert _nearest_expiration([], date.today(), 30) is None

    def test_falls_back_to_closest_if_all_under_target(self):
        today = date(2026, 4, 1)
        exps = [(today + timedelta(days=5)).isoformat(), (today + timedelta(days=10)).isoformat()]
        # No exp ≥ 30 → fallback picks 10-day (closer to 30 than 5-day)
        assert _nearest_expiration(exps, today, 30) == exps[1]


class TestAtmIv:
    def test_returns_none_for_empty(self):
        assert _atm_iv(pd.DataFrame(columns=["strike", "impliedVolatility"]),
                      pd.DataFrame(columns=["strike", "impliedVolatility"]), 100.0) is None

    def test_averages_call_and_put(self):
        calls = _chain_df([95, 100, 105], [0.30, 0.32, 0.34])
        puts = _chain_df([95, 100, 105], [0.40, 0.36, 0.32])
        # Spot 100 → ATM call IV 0.32, ATM put IV 0.36 → avg 0.34
        assert _atm_iv(calls, puts, 100.0) == pytest.approx(0.34)

    def test_skips_zero_iv(self):
        calls = _chain_df([100], [0.0])
        puts = _chain_df([100], [0.40])
        assert _atm_iv(calls, puts, 100.0) == pytest.approx(0.40)


class TestSkewIv:
    def test_picks_nearest_strike(self):
        df = _chain_df([90, 95, 100, 105, 110], [0.45, 0.40, 0.35, 0.32, 0.30])
        # Target 92 → nearest strike 90 → 0.45
        assert _skew_iv(df, 92.0) == pytest.approx(0.45)


class TestFetcherEndToEnd:
    def test_no_options_writes_flagged_row(self, db):
        _seed_price(db, "AAPL", 150.0)
        fetcher = OptionsFetcher(db=db, sleep_s=0)

        with patch("src.pipeline.options_fetcher.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.options = []
            status = fetcher.fetch_one("AAPL", date.today())

        assert status == "no_options"
        snap = db.query(OptionsSnapshot).filter_by(ticker="AAPL").one()
        assert snap.has_options == 0
        assert snap.iv_atm_30d is None

    def test_full_snapshot(self, db):
        spot = 100.0
        _seed_price(db, "AAPL", spot)
        today = date(2026, 4, 1)

        exp_30 = (today + timedelta(days=30)).isoformat()
        exp_90 = (today + timedelta(days=90)).isoformat()

        chain_30 = MagicMock()
        chain_30.calls = _chain_df([90, 100, 110], [0.30, 0.32, 0.34])
        chain_30.puts = _chain_df([90, 100, 110], [0.40, 0.36, 0.32])

        chain_90 = MagicMock()
        chain_90.calls = _chain_df([90, 100, 110], [0.28, 0.30, 0.32])
        chain_90.puts = _chain_df([90, 100, 110], [0.36, 0.32, 0.30])

        def chain_for(exp):
            return chain_30 if exp == exp_30 else chain_90

        with patch("src.pipeline.options_fetcher.yf.Ticker") as mock_ticker:
            stock = MagicMock()
            stock.options = [exp_30, exp_90]
            stock.option_chain.side_effect = chain_for
            mock_ticker.return_value = stock

            fetcher = OptionsFetcher(db=db, sleep_s=0)
            status = fetcher.fetch_one("AAPL", today)

        assert status == "ok"
        snap = db.query(OptionsSnapshot).filter_by(ticker="AAPL").one()
        assert snap.has_options == 1
        # ATM 30 = avg(0.32, 0.36) = 0.34
        assert snap.iv_atm_30d == pytest.approx(0.34)
        # ATM 90 = avg(0.30, 0.32) = 0.31
        assert snap.iv_atm_90d == pytest.approx(0.31)
        # Term slope = 0.31 - 0.34 = -0.03
        assert snap.term_structure_slope == pytest.approx(-0.03)
        # Skew = put_iv@90 (0.40) - call_iv@110 (0.34) = 0.06
        assert snap.put_call_skew_25d == pytest.approx(0.06)

    def test_iv_rank_uses_history(self, db):
        # Seed 5 prior snapshots with rising IV: 0.20, 0.22, 0.24, 0.26, 0.28
        for i, iv in enumerate([0.20, 0.22, 0.24, 0.26, 0.28]):
            db.add(OptionsSnapshot(
                ticker="AAPL",
                date=date.today() - timedelta(days=10 - i),
                iv_atm_30d=iv,
                has_options=1,
            ))
        db.commit()

        fetcher = OptionsFetcher(db=db, sleep_s=0)
        rank, pct = fetcher._iv_rank_and_percentile("AAPL", current_iv=0.30)
        # Range [0.20, 0.30] → rank = (0.30-0.20)/(0.30-0.20) = 1.0
        assert rank == pytest.approx(1.0)
        # All 5 historical IVs < 0.30; current is the 6th. Percentile = 5/6.
        assert pct == pytest.approx(5 / 6, rel=1e-3)

    def test_rank_with_no_history_is_zero(self, db):
        fetcher = OptionsFetcher(db=db, sleep_s=0)
        rank, pct = fetcher._iv_rank_and_percentile("AAPL", current_iv=0.30)
        assert rank == 0.0
        assert pct == 0.0

    def test_yfinance_error_writes_flagged_row(self, db):
        _seed_price(db, "AAPL", 150.0)
        fetcher = OptionsFetcher(db=db, sleep_s=0)

        with patch("src.pipeline.options_fetcher.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.options = ["bad-date"]  # _nearest_expiration skips it
            mock_ticker.return_value.option_chain.side_effect = RuntimeError("boom")
            status = fetcher.fetch_one("AAPL", date.today())

        # No usable expirations → treated as no_options
        assert status == "no_options"

    def test_upsert_replaces_same_day(self, db):
        _seed_price(db, "AAPL", 150.0)
        fetcher = OptionsFetcher(db=db, sleep_s=0)

        with patch("src.pipeline.options_fetcher.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.options = []
            fetcher.fetch_one("AAPL", date.today())
            fetcher.fetch_one("AAPL", date.today())

        assert db.query(OptionsSnapshot).filter_by(ticker="AAPL").count() == 1
