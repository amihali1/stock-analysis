"""Wikipedia page-view features (P10-008).

Retail-attention proxy built from `wikipedia_pageviews`. Wikimedia returns
per-article daily counts; the fetcher stubs missing days with 0 so the series
is dense for rolling-window math.

Features (computed as of an `on_date`):
- wiki_views_zscore_30d  : z-score of today's views vs trailing 30d (excluding today)
- wiki_views_zscore_180d : same on a 180d baseline
- wiki_views_change_7d   : (last-7d mean − prior-7d mean) / max(prior-7d mean, 1)
- wiki_views_spike       : 1 if today > 3 × trailing-30d mean
- wiki_views_log         : log1p(today's views)

Weekend behavior: trading days are weekdays-only but Wikipedia data is
every day. Forward-fill into Monday by taking the most-recent view_date ≤
the trading day's prediction-as-of date (`pd.merge_asof` with
direction='backward').
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from src.db.models import WikipediaPageviews

WIKIPEDIA_FEATURE_COLS = [
    "wiki_views_zscore_30d",
    "wiki_views_zscore_180d",
    "wiki_views_change_7d",
    "wiki_views_spike",
    "wiki_views_log",
]

DEFAULT_WIKIPEDIA_FEATURES: dict[str, float] = {
    "wiki_views_zscore_30d": 0.0,
    "wiki_views_zscore_180d": 0.0,
    "wiki_views_change_7d": 0.0,
    "wiki_views_spike": 0.0,
    "wiki_views_log": 0.0,
}

SPIKE_RATIO = 3.0
ZSCORE_30D_WINDOW = 30
ZSCORE_180D_WINDOW = 180
CHANGE_WINDOW = 7
ZSCORE_MIN_OBS = 5
CHANGE_MIN_OBS = 3


def _compute_frame(views: pd.Series) -> pd.DataFrame:
    """Compute the feature frame from a views Series indexed by view_date.

    Rolling windows exclude today (`shift(1)`) for the z-scores so the spike
    of-the-day shows up in the numerator, not the baseline. The 7d change
    window includes today on the recent side and is shifted by 7 for the
    prior side.
    """
    views = views.astype(float).sort_index()

    prior_30_mean = views.shift(1).rolling(ZSCORE_30D_WINDOW, min_periods=ZSCORE_MIN_OBS).mean()
    prior_30_std = views.shift(1).rolling(ZSCORE_30D_WINDOW, min_periods=ZSCORE_MIN_OBS).std()
    prior_180_mean = views.shift(1).rolling(ZSCORE_180D_WINDOW, min_periods=ZSCORE_MIN_OBS).mean()
    prior_180_std = views.shift(1).rolling(ZSCORE_180D_WINDOW, min_periods=ZSCORE_MIN_OBS).std()

    z_30 = (views - prior_30_mean) / prior_30_std.replace(0, np.nan)
    z_180 = (views - prior_180_mean) / prior_180_std.replace(0, np.nan)

    last_7_mean = views.rolling(CHANGE_WINDOW, min_periods=CHANGE_MIN_OBS).mean()
    prior_7_mean = views.shift(CHANGE_WINDOW).rolling(
        CHANGE_WINDOW, min_periods=CHANGE_MIN_OBS
    ).mean()
    change_7 = (last_7_mean - prior_7_mean) / prior_7_mean.where(prior_7_mean > 0, np.nan)

    spike = (views > SPIKE_RATIO * prior_30_mean).astype(float)
    spike = spike.where(prior_30_mean.notna(), 0.0)

    feats = pd.DataFrame(
        {
            "wiki_views_zscore_30d": z_30.fillna(0.0).clip(-10, 10),
            "wiki_views_zscore_180d": z_180.fillna(0.0).clip(-10, 10),
            "wiki_views_change_7d": change_7.fillna(0.0).clip(-10, 10),
            "wiki_views_spike": spike.fillna(0.0),
            "wiki_views_log": np.log1p(views.clip(lower=0)),
        }
    )
    return feats


def _load_views(db: Session, tickers: Iterable[str]) -> dict[str, pd.Series]:
    """Load page-view series per ticker as {ticker: Series indexed by view_date}."""
    tickers = list(tickers)
    if not tickers:
        return {}
    rows = (
        db.query(
            WikipediaPageviews.ticker,
            WikipediaPageviews.view_date,
            WikipediaPageviews.page_views,
        )
        .filter(WikipediaPageviews.ticker.in_(tickers))
        .order_by(WikipediaPageviews.ticker.asc(), WikipediaPageviews.view_date.asc())
        .all()
    )
    grouped: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for ticker, view_date, page_views in rows:
        grouped[ticker].append((view_date, int(page_views or 0)))

    out: dict[str, pd.Series] = {}
    for ticker, pairs in grouped.items():
        idx = pd.to_datetime([p[0] for p in pairs])
        vals = [p[1] for p in pairs]
        out[ticker] = pd.Series(vals, index=idx, name="page_views")
    return out


def get_wikipedia_features(
    db: Session, ticker: str, on_date: date | None = None
) -> dict[str, float]:
    """Single-row feature lookup used at inference time."""
    on_date = on_date or date.today()
    series_by_t = _load_views(db, [ticker])
    series = series_by_t.get(ticker)
    if series is None or series.empty:
        return dict(DEFAULT_WIKIPEDIA_FEATURES)

    cutoff = pd.Timestamp(on_date)
    series = series.loc[series.index <= cutoff]
    if series.empty:
        return dict(DEFAULT_WIKIPEDIA_FEATURES)

    feats = _compute_frame(series)
    last = feats.iloc[-1]
    return {
        col: float(last[col]) if not (
            isinstance(last[col], float) and math.isnan(last[col])
        ) else DEFAULT_WIKIPEDIA_FEATURES[col]
        for col in WIKIPEDIA_FEATURE_COLS
    }


def attach_wikipedia_features(db: Session, df: pd.DataFrame) -> pd.DataFrame:
    """As-of join Wikipedia features onto a (ticker, date) DataFrame.

    Uses pd.merge_asof per the macro/options pattern: each (ticker, date) row
    is matched to the most recent view_date ≤ date, forward-filling weekends
    into Monday.
    """
    if df.empty:
        for col, default in DEFAULT_WIKIPEDIA_FEATURES.items():
            df[col] = default
        return df

    tickers = df["ticker"].unique().tolist()
    series_by_t = _load_views(db, tickers)

    if not series_by_t:
        out = df.copy()
        for col, default in DEFAULT_WIKIPEDIA_FEATURES.items():
            out[col] = default
        return out

    snap_frames: list[pd.DataFrame] = []
    for ticker, series in series_by_t.items():
        feats = _compute_frame(series)
        feats = feats.reset_index().rename(columns={"index": "date"})
        feats["ticker"] = ticker
        snap_frames.append(feats)

    snaps = pd.concat(snap_frames, ignore_index=True)
    snaps["date"] = pd.to_datetime(snaps["date"])
    snaps = snaps.sort_values("date").reset_index(drop=True)

    out = df.copy()
    out["_orig_date"] = out["date"]
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values("date").reset_index(drop=True)

    merged = pd.merge_asof(
        out,
        snaps,
        by="ticker",
        on="date",
        direction="backward",
    )
    merged["date"] = merged["_orig_date"]
    merged = merged.drop(columns=["_orig_date"])

    for col, default in DEFAULT_WIKIPEDIA_FEATURES.items():
        if col not in merged.columns:
            merged[col] = default
        else:
            merged[col] = merged[col].fillna(default)
    return merged
