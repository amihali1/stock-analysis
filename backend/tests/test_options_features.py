"""Tests for options-derived feature extraction."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, OptionsSnapshot, Stock
from src.features.options import (
    DEFAULT_FEATURES,
    OPTIONS_FEATURE_COLS,
    attach_options_features,
    get_options_features,
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


class TestGetOptionsFeatures:
    def test_missing_returns_defaults(self, db):
        feats = get_options_features(db, "AAPL")
        assert feats == DEFAULT_FEATURES
        assert feats["has_options"] == 0.0

    def test_returns_latest_snapshot(self, db):
        db.add(OptionsSnapshot(
            ticker="AAPL", date=date.today() - timedelta(days=2),
            iv_atm_30d=0.25, iv_rank_252d=0.10, iv_percentile_252d=0.20,
            put_call_skew_25d=0.01, term_structure_slope=0.02, has_options=1,
        ))
        db.add(OptionsSnapshot(
            ticker="AAPL", date=date.today(),
            iv_atm_30d=0.40, iv_rank_252d=0.80, iv_percentile_252d=0.75,
            put_call_skew_25d=0.05, term_structure_slope=0.03, has_options=1,
        ))
        db.commit()

        feats = get_options_features(db, "AAPL")
        assert feats["iv_atm_30d"] == 0.40
        assert feats["iv_rank_252d"] == 0.80
        assert feats["has_options"] == 1.0

    def test_has_options_zero_falls_back_to_defaults(self, db):
        db.add(OptionsSnapshot(
            ticker="AAPL", date=date.today(),
            iv_atm_30d=None, has_options=0,
        ))
        db.commit()

        feats = get_options_features(db, "AAPL")
        # has_options=0 → all numeric values use defaults
        assert feats["iv_atm_30d"] == DEFAULT_FEATURES["iv_atm_30d"]
        assert feats["has_options"] == 0.0

    def test_respects_on_date_cutoff(self, db):
        db.add(OptionsSnapshot(
            ticker="AAPL", date=date(2026, 1, 1),
            iv_atm_30d=0.25, has_options=1,
        ))
        db.add(OptionsSnapshot(
            ticker="AAPL", date=date(2026, 4, 1),
            iv_atm_30d=0.40, has_options=1,
        ))
        db.commit()

        feats = get_options_features(db, "AAPL", on_date=date(2026, 2, 1))
        assert feats["iv_atm_30d"] == 0.25


class TestAttachOptionsFeatures:
    def test_empty_df_gets_default_columns(self, db):
        df = pd.DataFrame(columns=["ticker", "date"])
        out = attach_options_features(db, df)
        for col in OPTIONS_FEATURE_COLS:
            assert col in out.columns

    def test_asof_join(self, db):
        # Snapshot on day 5 with iv 0.40; rows on days 3, 6, 8 → first row has no
        # snapshot ≤ its date so it gets defaults; later rows pick up 0.40.
        db.add(OptionsSnapshot(
            ticker="AAPL", date=date(2026, 4, 5),
            iv_atm_30d=0.40, iv_rank_252d=0.6, iv_percentile_252d=0.7,
            put_call_skew_25d=0.02, term_structure_slope=0.01, has_options=1,
        ))
        db.commit()

        df = pd.DataFrame([
            {"ticker": "AAPL", "date": date(2026, 4, 3)},
            {"ticker": "AAPL", "date": date(2026, 4, 6)},
            {"ticker": "AAPL", "date": date(2026, 4, 8)},
        ])
        out = attach_options_features(db, df)
        assert out.iloc[0]["iv_atm_30d"] == DEFAULT_FEATURES["iv_atm_30d"]
        assert out.iloc[0]["has_options"] == 0.0
        assert out.iloc[1]["iv_atm_30d"] == 0.40
        assert out.iloc[2]["iv_atm_30d"] == 0.40
        assert out.iloc[2]["has_options"] == 1.0

    def test_missing_ticker_uses_defaults(self, db):
        # Snapshot exists for AAPL but not MSFT
        db.add(OptionsSnapshot(
            ticker="AAPL", date=date(2026, 4, 5),
            iv_atm_30d=0.40, has_options=1,
        ))
        db.commit()

        df = pd.DataFrame([
            {"ticker": "AAPL", "date": date(2026, 4, 6)},
            {"ticker": "MSFT", "date": date(2026, 4, 6)},
        ])
        out = attach_options_features(db, df).sort_values("ticker").reset_index(drop=True)
        msft_row = out[out["ticker"] == "MSFT"].iloc[0]
        assert msft_row["iv_atm_30d"] == DEFAULT_FEATURES["iv_atm_30d"]
        assert msft_row["has_options"] == 0.0
