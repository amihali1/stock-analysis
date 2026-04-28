"""Market-regime features (SPY trend, VIX level, drawdown).

Joined onto each per-ticker training row by date so the directional model can
condition on broad market context, not just per-name technicals.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from src.db.models import PriceHistory

MACRO_FEATURE_COLS = [
    "vix_level",
    "vix_percentile_252d",
    "spy_drawdown_pct",
    "spy_above_sma_50",
    "spy_above_sma_200",
    "spy_return_5d",
    "spy_return_20d",
]

# Neutral defaults when a row has no SPY/VIX context (early dates, missing data).
DEFAULT_MACRO_FEATURES: dict[str, float] = {
    "vix_level": 18.0,            # near long-run median
    "vix_percentile_252d": 0.5,
    "spy_drawdown_pct": 0.0,
    "spy_above_sma_50": 1.0,
    "spy_above_sma_200": 1.0,
    "spy_return_5d": 0.0,
    "spy_return_20d": 0.0,
}


def _load_series(db: Session, ticker: str) -> pd.DataFrame:
    rows = (
        db.query(PriceHistory.date, PriceHistory.close)
        .filter(PriceHistory.ticker == ticker)
        .order_by(PriceHistory.date.asc())
        .all()
    )
    if not rows:
        return pd.DataFrame(columns=["date", "close"])
    df = pd.DataFrame(rows, columns=["date", "close"])
    df["close"] = df["close"].astype(float)
    return df


def _build_macro_frame(db: Session) -> pd.DataFrame:
    """Build a per-date macro feature frame from SPY + ^VIX history."""
    spy = _load_series(db, "SPY").rename(columns={"close": "spy_close"})
    vix = _load_series(db, "^VIX").rename(columns={"close": "vix_close"})

    if spy.empty:
        return pd.DataFrame(columns=["date", *MACRO_FEATURE_COLS])

    # Outer-join SPY and VIX on date so VIX holiday gaps don't drop SPY rows.
    macro = spy.merge(vix, on="date", how="left").sort_values("date").reset_index(drop=True)

    macro["vix_close"] = macro["vix_close"].ffill()  # forward-fill VIX gaps only
    macro["vix_level"] = macro["vix_close"]
    macro["vix_percentile_252d"] = (
        macro["vix_close"].rolling(252, min_periods=20).rank(pct=True)
    )

    macro["spy_max_252d"] = macro["spy_close"].rolling(252, min_periods=20).max()
    macro["spy_drawdown_pct"] = macro["spy_close"] / macro["spy_max_252d"] - 1.0

    macro["spy_sma_50"] = macro["spy_close"].rolling(50, min_periods=10).mean()
    macro["spy_sma_200"] = macro["spy_close"].rolling(200, min_periods=20).mean()
    macro["spy_above_sma_50"] = (macro["spy_close"] > macro["spy_sma_50"]).astype(float)
    macro["spy_above_sma_200"] = (macro["spy_close"] > macro["spy_sma_200"]).astype(float)

    macro["spy_return_5d"] = macro["spy_close"].pct_change(5)
    macro["spy_return_20d"] = macro["spy_close"].pct_change(20)

    return macro[["date", *MACRO_FEATURE_COLS]]


def get_macro_features(db: Session, on_date: date | None = None) -> dict[str, float]:
    """Return macro features for the most recent date at or before `on_date`."""
    macro = _build_macro_frame(db)
    if macro.empty:
        return dict(DEFAULT_MACRO_FEATURES)

    if on_date is not None:
        macro = macro[macro["date"] <= on_date]
    if macro.empty:
        return dict(DEFAULT_MACRO_FEATURES)

    row = macro.iloc[-1]
    out: dict[str, float] = {}
    for col, default in DEFAULT_MACRO_FEATURES.items():
        val = row.get(col)
        out[col] = float(val) if pd.notna(val) else default
    return out


def attach_macro_features(db: Session, df: pd.DataFrame) -> pd.DataFrame:
    """As-of-join macro features onto a DataFrame keyed by ['date']."""
    if df.empty:
        for col, default in DEFAULT_MACRO_FEATURES.items():
            df[col] = default
        return df

    macro = _build_macro_frame(db)
    if macro.empty:
        for col, default in DEFAULT_MACRO_FEATURES.items():
            df[col] = default
        return df

    out = df.copy()
    out["_orig_date"] = out["date"]
    out["date"] = pd.to_datetime(out["date"])
    macro["date"] = pd.to_datetime(macro["date"])

    out = out.sort_values("date").reset_index(drop=True)
    macro = macro.sort_values("date").reset_index(drop=True)

    merged = pd.merge_asof(out, macro, on="date", direction="backward")
    merged["date"] = merged["_orig_date"]
    merged = merged.drop(columns=["_orig_date"])

    for col, default in DEFAULT_MACRO_FEATURES.items():
        if col not in merged.columns:
            merged[col] = default
        else:
            merged[col] = merged[col].fillna(default)
    return merged
