"""Tests for sentiment time-series features (P9-004)."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, SentimentHistory, Stock
from src.features.sentiment import (
    DEFAULT_SENTIMENT_FEATURES,
    SENTIMENT_FEATURE_COLS,
    attach_sentiment_features,
    get_sentiment_features,
    upsert_daily_sentiment,
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


def _seed(db, ticker, start, scores, articles=None):
    articles = articles or [1] * len(scores)
    for i, (sc, ac) in enumerate(zip(scores, articles)):
        db.add(SentimentHistory(
            ticker=ticker, date=start + timedelta(days=i),
            sentiment_score=sc, confidence=0.8, article_count=ac,
        ))
    db.commit()


def test_no_history_defaults(db):
    feats = get_sentiment_features(db, "AAPL")
    assert feats == DEFAULT_SENTIMENT_FEATURES


def test_basic_zscore(db):
    start = date(2025, 1, 1)
    # 30 days of -0.1 sentiment then a +0.5 spike
    scores = [-0.1] * 30 + [0.5]
    _seed(db, "AAPL", start, scores)

    feats = get_sentiment_features(db, "AAPL", on_date=start + timedelta(days=30))
    assert feats["sentiment_latest"] == pytest.approx(0.5)
    # 30-day MA includes the spike: mean ≈ -0.0806
    assert feats["sentiment_ma_30d"] == pytest.approx(-0.0806, abs=1e-3)
    # Momentum vs 7d MA — 7d window includes the spike, so smaller positive
    assert feats["sentiment_momentum"] > 0
    assert feats["sentiment_zscore_30d"] > 1.0  # spike is several std above mean


def test_constant_scores_no_div_by_zero(db):
    start = date(2025, 1, 1)
    _seed(db, "AAPL", start, [0.2] * 30)
    feats = get_sentiment_features(db, "AAPL", on_date=start + timedelta(days=29))
    assert feats["sentiment_zscore_30d"] == 0.0
    assert feats["article_count_zscore_30d"] == 0.0


def test_short_history_under_30d(db):
    start = date(2025, 1, 1)
    _seed(db, "AAPL", start, [0.1, 0.2, 0.3, 0.4, 0.5])
    feats = get_sentiment_features(db, "AAPL", on_date=start + timedelta(days=4))
    assert feats["sentiment_latest"] == pytest.approx(0.5)
    # Only 5 rows — z-score returns 0 only if std == 0; otherwise computed
    # Here scores are increasing so std > 0; just verify it's finite
    assert feats["sentiment_zscore_30d"] != 0  # changing values → some signal


def test_attach_join(db):
    start = date(2025, 1, 1)
    _seed(db, "AAPL", start, [0.1, 0.2, 0.3])

    df = pd.DataFrame([
        {"ticker": "AAPL", "date": start + timedelta(days=2)},
        {"ticker": "AAPL", "date": start + timedelta(days=10)},  # past last sentiment
    ])
    out = attach_sentiment_features(db, df)
    for col in SENTIMENT_FEATURE_COLS:
        assert col in out.columns
    assert out.iloc[0]["sentiment_latest"] == pytest.approx(0.3)
    assert out.iloc[1]["sentiment_latest"] == pytest.approx(0.3)


def test_upsert_replaces_same_day(db):
    upsert_daily_sentiment(db, "AAPL", date(2025, 1, 1), 0.2, 0.8, 5)
    upsert_daily_sentiment(db, "AAPL", date(2025, 1, 1), 0.4, 0.9, 7)
    rows = db.query(SentimentHistory).filter_by(ticker="AAPL").all()
    assert len(rows) == 1
    assert rows[0].sentiment_score == pytest.approx(0.4)
    assert rows[0].article_count == 7
