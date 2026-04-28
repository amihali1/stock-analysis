"""Sentiment time-series features.

The legacy `SentimentScore` table has one row per article. `SentimentHistory`
adds a daily aggregate so we can compute moving averages, momentum, and
z-scored news-volume surprise.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from src.db.models import SentimentHistory

SENTIMENT_FEATURE_COLS = [
    "sentiment_latest",
    "sentiment_ma_7d",
    "sentiment_ma_30d",
    "sentiment_momentum",
    "sentiment_zscore_30d",
    "article_count_zscore_30d",
]

DEFAULT_SENTIMENT_FEATURES: dict[str, float] = {
    "sentiment_latest": 0.0,
    "sentiment_ma_7d": 0.0,
    "sentiment_ma_30d": 0.0,
    "sentiment_momentum": 0.0,
    "sentiment_zscore_30d": 0.0,
    "article_count_zscore_30d": 0.0,
}


def _safe_z(value: float, mean: float, std: float) -> float:
    # Tolerant equality — pandas std() of nearly-identical floats can leak
    # ~1e-17 of residual which would otherwise produce nonsense z-scores.
    if pd.isna(std) or abs(std) < 1e-9:
        return 0.0
    return float((value - mean) / std)


def _ticker_history(db: Session, ticker: str, on_date: date | None = None) -> pd.DataFrame:
    q = db.query(
        SentimentHistory.date,
        SentimentHistory.sentiment_score,
        SentimentHistory.article_count,
    ).filter(SentimentHistory.ticker == ticker)
    if on_date is not None:
        q = q.filter(SentimentHistory.date <= on_date)
    rows = q.order_by(SentimentHistory.date.asc()).all()
    if not rows:
        return pd.DataFrame(columns=["date", "sentiment_score", "article_count"])
    df = pd.DataFrame(rows, columns=["date", "sentiment_score", "article_count"])
    df["sentiment_score"] = df["sentiment_score"].astype(float).fillna(0.0)
    df["article_count"] = df["article_count"].astype(float).fillna(0.0)
    return df


def _features_from_series(df: pd.DataFrame) -> dict[str, float]:
    if df.empty:
        return dict(DEFAULT_SENTIMENT_FEATURES)

    latest_score = float(df["sentiment_score"].iloc[-1])
    last7 = df["sentiment_score"].tail(7)
    last30 = df["sentiment_score"].tail(30)
    art30 = df["article_count"].tail(30)

    ma_7 = float(last7.mean()) if len(last7) else 0.0
    ma_30 = float(last30.mean()) if len(last30) else 0.0

    sentiment_z = _safe_z(latest_score, ma_30, float(last30.std(ddof=0))) if len(last30) >= 2 else 0.0
    article_z = (
        _safe_z(float(df["article_count"].iloc[-1]), float(art30.mean()), float(art30.std(ddof=0)))
        if len(art30) >= 2 else 0.0
    )

    return {
        "sentiment_latest": latest_score,
        "sentiment_ma_7d": ma_7,
        "sentiment_ma_30d": ma_30,
        "sentiment_momentum": latest_score - ma_7,
        "sentiment_zscore_30d": sentiment_z,
        "article_count_zscore_30d": article_z,
    }


def get_sentiment_features(db: Session, ticker: str, on_date: date | None = None) -> dict[str, float]:
    df = _ticker_history(db, ticker, on_date)
    return _features_from_series(df)


def attach_sentiment_features(db: Session, df: pd.DataFrame) -> pd.DataFrame:
    """Attach sentiment time-series features to a per-(ticker, date) DataFrame.

    Implementation note: this calls `get_sentiment_features` per row. That's
    O(rows × tickers) but acceptable here — the directional dataset is rebuilt
    daily, not per-request, and history reads are cached at the SQL layer.
    """
    if df.empty:
        for col, default in DEFAULT_SENTIMENT_FEATURES.items():
            df[col] = default
        return df

    out = df.copy()
    feature_rows = []
    cache: dict[str, pd.DataFrame] = {}
    for _, row in out.iterrows():
        ticker = row["ticker"]
        on_date = row["date"]
        if ticker not in cache:
            cache[ticker] = _ticker_history(db, ticker)
        history = cache[ticker]
        if not history.empty:
            history_to_date = history[history["date"] <= on_date]
        else:
            history_to_date = history
        feature_rows.append(_features_from_series(history_to_date))

    feats_df = pd.DataFrame(feature_rows)
    for col in SENTIMENT_FEATURE_COLS:
        out[col] = feats_df[col].values
    return out


def upsert_daily_sentiment(
    db: Session,
    ticker: str,
    on_date: date,
    sentiment_score: float | None,
    confidence: float | None,
    article_count: int,
) -> None:
    """Upsert one daily aggregated sentiment row for a ticker."""
    existing = (
        db.query(SentimentHistory)
        .filter_by(ticker=ticker, date=on_date)
        .first()
    )
    if existing is None:
        existing = SentimentHistory(ticker=ticker, date=on_date)
        db.add(existing)
    existing.sentiment_score = sentiment_score
    existing.confidence = confidence
    existing.article_count = article_count
    db.commit()
