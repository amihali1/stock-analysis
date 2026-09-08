"""Tests for Information Coefficient computation (src/services/ic_metrics.py)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, IcMetric, PriceHistory, SignalScore, Stock
from src.services.ic_metrics import (
    compute_and_store_ic,
    rolling_ic_summary,
    spearman,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


class TestSpearman:
    def test_perfect_positive(self):
        assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)

    def test_perfect_negative(self):
        assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)

    def test_monotonic_nonlinear_is_one(self):
        # Spearman uses ranks, so a monotone nonlinear relation is still 1.0.
        assert spearman([1, 2, 3, 4], [1, 4, 9, 16]) == pytest.approx(1.0)

    def test_too_few_points(self):
        assert spearman([1, 2], [1, 2]) is None

    def test_no_variance(self):
        assert spearman([1, 1, 1, 1], [1, 2, 3, 4]) is None


def _seed_prices(db, ticker: str, start: date, closes: list[float]):
    db.add(Stock(ticker=ticker))
    for i, c in enumerate(closes):
        d = start + timedelta(days=i)
        db.add(PriceHistory(ticker=ticker, date=d, close=c, adj_close=c))


def _seed_score(db, d: date, ticker: str, direction: str, rise=None, drop=None, comp=0.5):
    db.add(SignalScore(
        date=d, ticker=ticker, direction=direction,
        rise_prob=rise, drop_prob=drop, predicted_vol=0.3,
        sentiment_score=0.0, sentiment_confidence=0.8,
        composite_score=comp, selected=False,
    ))


class TestComputeIC:
    def test_positive_ic_when_signal_ranks_returns(self, db):
        """rise_prob that ranks forward returns correctly → positive IC."""
        start = date.today() - timedelta(days=40)
        # 3 names; higher rise_prob → higher forward return (5 sessions out).
        # Give 6 sessions of prices so a 5d forward return exists on day 0.
        _seed_prices(db, "AAA", start, [100, 100, 100, 100, 100, 110])  # +10%
        _seed_prices(db, "BBB", start, [100, 100, 100, 100, 100, 105])  # +5%
        _seed_prices(db, "CCC", start, [100, 100, 100, 100, 100, 100])  # 0%
        _seed_score(db, start, "AAA", "rise", rise=0.30, comp=0.30)
        _seed_score(db, start, "BBB", "rise", rise=0.20, comp=0.20)
        _seed_score(db, start, "CCC", "rise", rise=0.10, comp=0.10)
        db.commit()

        summary = compute_and_store_ic(db, lookback_days=60)
        assert summary["written"] > 0
        rows = db.query(IcMetric).filter_by(signal="rise_prob", horizon=5).all()
        assert len(rows) == 1
        assert rows[0].ic == pytest.approx(1.0)  # perfectly ranked
        assert rows[0].n == 3

    def test_drop_prob_sign_adjusted(self, db):
        """drop_prob predicting falls: higher drop_prob → lower fwd return → IC>0."""
        start = date.today() - timedelta(days=40)
        _seed_prices(db, "AAA", start, [100, 100, 100, 100, 100, 90])   # -10%
        _seed_prices(db, "BBB", start, [100, 100, 100, 100, 100, 95])   # -5%
        _seed_prices(db, "CCC", start, [100, 100, 100, 100, 100, 100])  # 0%
        _seed_score(db, start, "AAA", "drop", drop=0.30, comp=0.30)
        _seed_score(db, start, "BBB", "drop", drop=0.20, comp=0.20)
        _seed_score(db, start, "CCC", "drop", drop=0.10, comp=0.10)
        db.commit()

        compute_and_store_ic(db, lookback_days=60)
        row = db.query(IcMetric).filter_by(signal="drop_prob", horizon=5).one()
        assert row.ic == pytest.approx(1.0)  # sign-adjusted → good ranking is +1

    def test_negative_ic_when_inverted(self, db):
        """Inverted signal (the real bug) → negative IC."""
        start = date.today() - timedelta(days=40)
        _seed_prices(db, "AAA", start, [100, 100, 100, 100, 100, 110])
        _seed_prices(db, "BBB", start, [100, 100, 100, 100, 100, 105])
        _seed_prices(db, "CCC", start, [100, 100, 100, 100, 100, 100])
        _seed_score(db, start, "AAA", "rise", rise=0.10, comp=0.10)  # best return, worst score
        _seed_score(db, start, "BBB", "rise", rise=0.20, comp=0.20)
        _seed_score(db, start, "CCC", "rise", rise=0.30, comp=0.30)  # worst return, best score
        db.commit()

        compute_and_store_ic(db, lookback_days=60)
        row = db.query(IcMetric).filter_by(signal="rise_prob", horizon=5).one()
        assert row.ic == pytest.approx(-1.0)

    def test_upsert_idempotent(self, db):
        start = date.today() - timedelta(days=40)
        _seed_prices(db, "AAA", start, [100, 100, 100, 100, 100, 110])
        _seed_prices(db, "BBB", start, [100, 100, 100, 100, 100, 105])
        _seed_prices(db, "CCC", start, [100, 100, 100, 100, 100, 100])
        for t, r in (("AAA", 0.3), ("BBB", 0.2), ("CCC", 0.1)):
            _seed_score(db, start, t, "rise", rise=r, comp=r)
        db.commit()

        compute_and_store_ic(db, lookback_days=60)
        n1 = db.query(IcMetric).count()
        compute_and_store_ic(db, lookback_days=60)  # re-run
        n2 = db.query(IcMetric).count()
        assert n1 == n2  # no duplicate rows

    def test_rolling_summary(self, db):
        start = date.today() - timedelta(days=40)
        _seed_prices(db, "AAA", start, [100, 100, 100, 100, 100, 110])
        _seed_prices(db, "BBB", start, [100, 100, 100, 100, 100, 105])
        _seed_prices(db, "CCC", start, [100, 100, 100, 100, 100, 100])
        for t, r in (("AAA", 0.3), ("BBB", 0.2), ("CCC", 0.1)):
            _seed_score(db, start, t, "rise", rise=r, comp=r)
        db.commit()
        compute_and_store_ic(db, lookback_days=60)

        summary = rolling_ic_summary(db, window_days=60)
        rise = [s for s in summary if s["signal"] == "rise_prob" and s["horizon"] == 5]
        assert rise and rise[0]["mean_ic"] == pytest.approx(1.0)
        assert rise[0]["days"] == 1
