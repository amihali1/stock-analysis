"""Tests for insider-transaction features (P10-005)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, InsiderTransaction, Stock
from src.features.insider import (
    CLUSTER_BUY_INSIDER_THRESHOLD,
    DAYS_SINCE_CAP,
    DEFAULT_INSIDER_FEATURES,
    INSIDER_FEATURE_COLS,
    attach_insider_features,
    get_insider_features,
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


_NEXT_ACC = [0]


def _add(
    db,
    ticker: str,
    tx_date: date,
    code: str,
    shares: float = 1000,
    price: float = 100.0,
    insider: str = "John Doe",
    owned_after: float | None = 5000,
):
    """Add one insider_transactions row with a unique accession_number."""
    _NEXT_ACC[0] += 1
    db.add(InsiderTransaction(
        ticker=ticker,
        accession_number=f"acc-{_NEXT_ACC[0]}",
        filing_date=tx_date,
        transaction_date=tx_date,
        insider_name=insider,
        transaction_code=code,
        shares=shares if code == "P" else -shares,  # match fetcher's sign convention
        price_per_share=price,
        total_value=abs(shares) * price,
        shares_owned_after=owned_after,
        fetched_at=datetime.utcnow(),
    ))


# ----------------------------------------------------------------- contracts


class TestContracts:
    def test_default_dict_has_every_feature_col(self):
        assert set(DEFAULT_INSIDER_FEATURES.keys()) == set(INSIDER_FEATURE_COLS)

    def test_no_history_returns_default(self, db):
        feats = get_insider_features(db, "AAPL", on_date=date(2026, 5, 8))
        assert feats == DEFAULT_INSIDER_FEATURES

    def test_unknown_ticker_returns_default(self, db):
        feats = get_insider_features(db, "ZZZ", on_date=date(2026, 5, 8))
        assert feats == DEFAULT_INSIDER_FEATURES


# -------------------------------------------------------------- count windows


class TestCountWindows:
    def test_buys_30d_only_counts_p_in_window(self, db):
        today = date(2026, 5, 8)
        _add(db, "AAPL", today - timedelta(days=5), "P")
        _add(db, "AAPL", today - timedelta(days=20), "P")
        _add(db, "AAPL", today - timedelta(days=45), "P")  # outside 30d
        _add(db, "AAPL", today - timedelta(days=10), "S")   # not a buy
        _add(db, "AAPL", today - timedelta(days=10), "A")   # grant — ignored
        db.commit()

        feats = get_insider_features(db, "AAPL", on_date=today)
        assert feats["insider_buys_30d"] == 2.0
        assert feats["insider_sells_30d"] == 1.0

    def test_net_buy_value_sums_dollar_value(self, db):
        today = date(2026, 5, 8)
        _add(db, "AAPL", today - timedelta(days=5), "P", shares=1000, price=100.0)  # +$100k
        _add(db, "AAPL", today - timedelta(days=10), "S", shares=200, price=200.0)  # -$40k
        db.commit()

        feats = get_insider_features(db, "AAPL", on_date=today)
        assert feats["insider_net_buy_value_30d"] == pytest.approx(60_000.0)

    def test_30d_and_90d_windows_are_independent(self, db):
        today = date(2026, 5, 8)
        # Within 90d but outside 30d → only counted in 90d
        _add(db, "AAPL", today - timedelta(days=60), "P", shares=500, price=200.0)  # +$100k
        # Within 30d
        _add(db, "AAPL", today - timedelta(days=10), "S", shares=100, price=100.0)  # -$10k
        db.commit()

        feats = get_insider_features(db, "AAPL", on_date=today)
        assert feats["insider_net_buy_value_30d"] == pytest.approx(-10_000.0)
        assert feats["insider_net_buy_value_90d"] == pytest.approx(90_000.0)


# ----------------------------------------------------------- cluster buy flag


class TestClusterBuy:
    def test_below_threshold_is_zero(self, db):
        today = date(2026, 5, 8)
        # 2 distinct insiders, threshold is 3
        _add(db, "AAPL", today - timedelta(days=5), "P", insider="Alice")
        _add(db, "AAPL", today - timedelta(days=10), "P", insider="Bob")
        db.commit()
        assert get_insider_features(db, "AAPL", on_date=today)["insider_cluster_buy_30d"] == 0.0

    def test_at_threshold_is_one(self, db):
        today = date(2026, 5, 8)
        for i in range(CLUSTER_BUY_INSIDER_THRESHOLD):
            _add(db, "AAPL", today - timedelta(days=i + 1), "P", insider=f"Insider-{i}")
        db.commit()
        assert get_insider_features(db, "AAPL", on_date=today)["insider_cluster_buy_30d"] == 1.0

    def test_same_insider_multiple_buys_counts_once(self, db):
        today = date(2026, 5, 8)
        for i in range(5):
            _add(db, "AAPL", today - timedelta(days=i + 1), "P", insider="Alice")
        db.commit()
        # 1 distinct insider — below threshold
        assert get_insider_features(db, "AAPL", on_date=today)["insider_cluster_buy_30d"] == 0.0

    def test_buys_outside_30d_dont_count_toward_cluster(self, db):
        today = date(2026, 5, 8)
        # 2 inside 30d, 2 outside — total 4 distinct but only 2 in window
        _add(db, "AAPL", today - timedelta(days=5), "P", insider="A")
        _add(db, "AAPL", today - timedelta(days=10), "P", insider="B")
        _add(db, "AAPL", today - timedelta(days=40), "P", insider="C")
        _add(db, "AAPL", today - timedelta(days=50), "P", insider="D")
        db.commit()
        assert get_insider_features(db, "AAPL", on_date=today)["insider_cluster_buy_30d"] == 0.0


# ---------------------------------------------------------- buy/sell ratio


class TestBuySellRatio:
    def test_no_activity_in_window_returns_neutral(self, db):
        today = date(2026, 5, 8)
        # Buy from 6 months ago — outside 90d window
        _add(db, "AAPL", today - timedelta(days=200), "P")
        db.commit()
        feats = get_insider_features(db, "AAPL", on_date=today)
        assert feats["insider_buy_sell_ratio_90d"] == 0.5

    def test_pure_buys_returns_one(self, db):
        today = date(2026, 5, 8)
        for i in range(5):
            _add(db, "AAPL", today - timedelta(days=i * 5 + 1), "P")
        db.commit()
        assert get_insider_features(db, "AAPL", on_date=today)["insider_buy_sell_ratio_90d"] == 1.0

    def test_pure_sells_returns_zero(self, db):
        today = date(2026, 5, 8)
        for i in range(5):
            _add(db, "AAPL", today - timedelta(days=i * 5 + 1), "S")
        db.commit()
        assert get_insider_features(db, "AAPL", on_date=today)["insider_buy_sell_ratio_90d"] == 0.0

    def test_mixed_returns_buy_fraction(self, db):
        today = date(2026, 5, 8)
        for _ in range(3):
            _add(db, "AAPL", today - timedelta(days=10), "P")
        for _ in range(7):
            _add(db, "AAPL", today - timedelta(days=20), "S")
        db.commit()
        assert get_insider_features(db, "AAPL", on_date=today)["insider_buy_sell_ratio_90d"] == pytest.approx(0.3)


# --------------------------------------------------------------- days since


class TestDaysSince:
    def test_no_buys_returns_minus_one(self, db):
        today = date(2026, 5, 8)
        _add(db, "AAPL", today - timedelta(days=5), "S")
        db.commit()
        feats = get_insider_features(db, "AAPL", on_date=today)
        assert feats["days_since_insider_buy"] == -1.0
        assert feats["days_since_insider_sell"] == 5.0

    def test_picks_most_recent(self, db):
        today = date(2026, 5, 8)
        _add(db, "AAPL", today - timedelta(days=200), "P")
        _add(db, "AAPL", today - timedelta(days=10), "P")
        _add(db, "AAPL", today - timedelta(days=50), "P")
        db.commit()
        assert get_insider_features(db, "AAPL", on_date=today)["days_since_insider_buy"] == 10.0

    def test_caps_at_max(self, db):
        today = date(2026, 5, 8)
        _add(db, "AAPL", today - timedelta(days=DAYS_SINCE_CAP + 100), "P")
        db.commit()
        feats = get_insider_features(db, "AAPL", on_date=today)
        assert feats["days_since_insider_buy"] == float(DAYS_SINCE_CAP)


# ------------------------------------------------------- ownership change %


class TestOwnershipChange:
    def test_no_baseline_observation_returns_zero(self, db):
        today = date(2026, 5, 8)
        # Only one insider activity, all inside the window — no prior baseline
        _add(db, "AAPL", today - timedelta(days=10), "P", insider="A", owned_after=5000)
        db.commit()
        assert get_insider_features(db, "AAPL", on_date=today)["pct_insider_ownership_change_90d"] == 0.0

    def test_increase_yields_positive_pct(self, db):
        today = date(2026, 5, 8)
        # Baseline: 1000 shares 6 months ago. Current: 1500 in last 30d.
        _add(db, "AAPL", today - timedelta(days=180), "P", insider="A", owned_after=1000)
        _add(db, "AAPL", today - timedelta(days=10), "P", insider="A", owned_after=1500)
        db.commit()
        feats = get_insider_features(db, "AAPL", on_date=today)
        # (1500 - 1000) / 1000 * 100 = 50%
        assert feats["pct_insider_ownership_change_90d"] == pytest.approx(50.0)

    def test_aggregates_across_insiders(self, db):
        today = date(2026, 5, 8)
        # A: 1000 → 1500 (+500). B: 2000 → 1500 (-500). Net 3000 → 3000 = 0%
        _add(db, "AAPL", today - timedelta(days=180), "P", insider="A", owned_after=1000)
        _add(db, "AAPL", today - timedelta(days=10), "P", insider="A", owned_after=1500)
        _add(db, "AAPL", today - timedelta(days=180), "S", insider="B", owned_after=2000)
        _add(db, "AAPL", today - timedelta(days=10), "S", insider="B", owned_after=1500)
        db.commit()
        feats = get_insider_features(db, "AAPL", on_date=today)
        assert feats["pct_insider_ownership_change_90d"] == pytest.approx(0.0)


# ----------------------------------------------------- as-of date isolation


class TestAsOfDate:
    def test_only_uses_transactions_on_or_before_on_date(self, db):
        # Future activity should not leak into a historical training row.
        on_date = date(2026, 5, 1)
        _add(db, "AAPL", on_date - timedelta(days=5), "P")
        _add(db, "AAPL", on_date + timedelta(days=2), "P")  # future
        db.commit()
        feats = get_insider_features(db, "AAPL", on_date=on_date)
        assert feats["insider_buys_30d"] == 1.0


# --------------------------------------------- DataFrame attachment helper


class TestAttachInsiderFeatures:
    def test_empty_df_gets_columns_with_defaults(self, db):
        df = pd.DataFrame(columns=["ticker", "date"])
        out = attach_insider_features(db, df)
        for col, default in DEFAULT_INSIDER_FEATURES.items():
            assert col in out.columns
            # Empty rows but column exists — value list should match default value
            # for new rows (no rows here, so we just check presence).

    def test_attaches_per_row(self, db):
        today = date(2026, 5, 8)
        _add(db, "AAPL", today - timedelta(days=5), "P")
        _add(db, "MSFT", today - timedelta(days=10), "S")
        db.commit()

        df = pd.DataFrame([
            {"ticker": "AAPL", "date": today},
            {"ticker": "MSFT", "date": today},
            {"ticker": "ZZZ", "date": today},  # unknown — defaults
        ])
        out = attach_insider_features(db, df)
        assert out.iloc[0]["insider_buys_30d"] == 1.0
        assert out.iloc[1]["insider_sells_30d"] == 1.0
        assert out.iloc[2]["insider_buys_30d"] == DEFAULT_INSIDER_FEATURES["insider_buys_30d"]
        assert out.iloc[2]["insider_buy_sell_ratio_90d"] == DEFAULT_INSIDER_FEATURES["insider_buy_sell_ratio_90d"]

    def test_caches_per_ticker(self, db, monkeypatch):
        # Same ticker twice — DB should be hit once.
        from src.features import insider as insider_mod
        today = date(2026, 5, 8)
        _add(db, "AAPL", today - timedelta(days=5), "P")
        db.commit()

        original = insider_mod._load_transactions
        calls = {"n": 0}

        def counted(d, t):
            calls["n"] += 1
            return original(d, t)

        monkeypatch.setattr(insider_mod, "_load_transactions", counted)

        df = pd.DataFrame([
            {"ticker": "AAPL", "date": today},
            {"ticker": "AAPL", "date": today - timedelta(days=1)},
            {"ticker": "AAPL", "date": today - timedelta(days=2)},
        ])
        attach_insider_features(db, df)
        assert calls["n"] == 1
