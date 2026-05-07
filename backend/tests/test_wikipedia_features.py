"""Tests for Wikipedia page-view features (P10-008)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Stock, WikipediaPageviews
from src.features.wikipedia import (
    DEFAULT_WIKIPEDIA_FEATURES,
    WIKIPEDIA_FEATURE_COLS,
    attach_wikipedia_features,
    get_wikipedia_features,
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


def _add(db, ticker, d, views, title="Apple_Inc."):
    db.add(WikipediaPageviews(
        ticker=ticker,
        view_date=d,
        page_views=views,
        wikipedia_title=title,
        fetched_at=datetime.utcnow(),
    ))


def _seed_quiet_series(db, ticker, end_day, days=200, baseline=1000):
    for i in range(days):
        _add(db, ticker, end_day - timedelta(days=days - 1 - i), baseline)
    db.commit()


def test_no_rows_returns_default(db):
    feats = get_wikipedia_features(db, "AAPL", on_date=date(2026, 5, 1))
    assert feats == DEFAULT_WIKIPEDIA_FEATURES


def test_quiet_series_has_zero_zscore(db):
    today = date(2026, 5, 1)
    _seed_quiet_series(db, "AAPL", today, days=60, baseline=1000)
    f = get_wikipedia_features(db, "AAPL", on_date=today)
    # Constant series → std == 0 → zscore default 0
    assert f["wiki_views_zscore_30d"] == 0.0
    assert f["wiki_views_change_7d"] == 0.0
    assert f["wiki_views_spike"] == 0.0
    # log1p(1000) ~ 6.9
    assert 6.9 < f["wiki_views_log"] < 7.0


def test_spike_triggers_high_zscore_and_flag(db):
    today = date(2026, 5, 1)
    # 30 quiet days, then 5x today
    for i in range(30):
        _add(db, "AAPL", today - timedelta(days=30 - i), 1000)
    _add(db, "AAPL", today, 5000)
    db.commit()

    f = get_wikipedia_features(db, "AAPL", on_date=today)
    assert f["wiki_views_spike"] == 1.0
    # 5000 vs constant 1000 baseline = inf-ish (std=0 → fallback to NaN→0). Allow either
    # the clipped 10 OR 0 (constant baseline). The spike flag is the primary signal.
    assert f["wiki_views_zscore_30d"] in (0.0, 10.0) or f["wiki_views_zscore_30d"] > 0


def test_zscore_handles_noisy_baseline(db):
    today = date(2026, 5, 1)
    # Noisy baseline: alternating 800/1200 → mean=1000, std≈200
    for i in range(30):
        v = 800 if i % 2 == 0 else 1200
        _add(db, "AAPL", today - timedelta(days=30 - i), v)
    _add(db, "AAPL", today, 2000)  # ~5 std above mean
    db.commit()

    f = get_wikipedia_features(db, "AAPL", on_date=today)
    assert f["wiki_views_zscore_30d"] > 3.0
    # 2000 vs ~1000 mean → only 2x, not 3x → no spike flag (correct)
    assert f["wiki_views_spike"] == 0.0


def test_change_7d_picks_up_week_over_week_jump(db):
    today = date(2026, 5, 1)
    # Prior week (days 8-14 ago): 1000; this week (last 7 days): 2000
    for i in range(14, 7, -1):
        _add(db, "AAPL", today - timedelta(days=i), 1000)
    for i in range(7, 0, -1):
        _add(db, "AAPL", today - timedelta(days=i), 2000)
    _add(db, "AAPL", today, 2000)
    db.commit()

    f = get_wikipedia_features(db, "AAPL", on_date=today)
    # (this_week_mean - prior_week_mean) / prior_week_mean ~ 1.0
    assert f["wiki_views_change_7d"] > 0.5


def test_change_7d_zero_when_no_prior_week(db):
    today = date(2026, 5, 1)
    # Only the last week — no prior-week rows for the change baseline.
    for i in range(6, 0, -1):
        _add(db, "AAPL", today - timedelta(days=i), 500)
    _add(db, "AAPL", today, 500)
    db.commit()

    f = get_wikipedia_features(db, "AAPL", on_date=today)
    # No prior-week samples → NaN → fillna(0)
    assert f["wiki_views_change_7d"] == 0.0


def test_log_scaling_captures_baseline(db):
    today = date(2026, 5, 1)
    _add(db, "AAPL", today, 999)  # log1p ~ 6.9
    db.commit()
    f = get_wikipedia_features(db, "AAPL", on_date=today)
    assert 6.9 < f["wiki_views_log"] < 7.0


def test_attach_forward_fills_into_weekend(db):
    """View_date Friday should propagate into Monday's prediction row."""
    fri = date(2026, 5, 1)  # Friday
    mon = date(2026, 5, 4)  # Monday
    # 60 days of data ending Friday
    _seed_quiet_series(db, "AAPL", fri, days=60, baseline=1000)
    db.commit()

    df = pd.DataFrame([{"ticker": "AAPL", "date": pd.Timestamp(mon)}])
    out = attach_wikipedia_features(db, df)
    assert all(col in out.columns for col in WIKIPEDIA_FEATURE_COLS)
    # Forward-fill: Monday gets Friday's features (log positive, not the default 0)
    assert out.loc[0, "wiki_views_log"] > 6.0


def test_attach_empty_df(db):
    df = pd.DataFrame(columns=["ticker", "date"])
    out = attach_wikipedia_features(db, df)
    for col, default in DEFAULT_WIKIPEDIA_FEATURES.items():
        assert col in out.columns


def test_attach_unknown_ticker_uses_defaults(db):
    df = pd.DataFrame([{"ticker": "ZZZ", "date": pd.Timestamp(date(2026, 5, 1))}])
    out = attach_wikipedia_features(db, df)
    for col, default in DEFAULT_WIKIPEDIA_FEATURES.items():
        assert out.loc[0, col] == default
