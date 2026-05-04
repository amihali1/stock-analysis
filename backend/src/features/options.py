"""Options-derived features for the directional model.

Reads `OptionsSnapshot` rows and exposes a feature vector per (ticker, date).
Falls back to neutral defaults when options data is missing so the model can
still score non-optionable tickers without column-shape errors.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from src.db.models import OptionsSnapshot

OPTIONS_FEATURE_COLS = [
    "iv_atm_30d",
    "iv_rank_252d",
    "iv_percentile_252d",
    "put_call_skew_25d",
    "term_structure_slope",
    "has_options",
]

# Neutral defaults used when options data is absent.
DEFAULT_FEATURES: dict[str, float] = {
    "iv_atm_30d": 0.30,           # plausible mid-IV for a typical equity
    "iv_rank_252d": 0.5,
    "iv_percentile_252d": 0.5,
    "put_call_skew_25d": 0.0,
    "term_structure_slope": 0.0,
    "has_options": 0.0,
}


def get_options_features(db: Session, ticker: str, on_date: date | None = None) -> dict[str, float]:
    """Return options features for a ticker at (or before) a given date."""
    q = db.query(OptionsSnapshot).filter(OptionsSnapshot.ticker == ticker)
    if on_date is not None:
        q = q.filter(OptionsSnapshot.date <= on_date)
    snap = q.order_by(OptionsSnapshot.date.desc()).first()
    if snap is None:
        return dict(DEFAULT_FEATURES)
    return _row_to_features(snap)


def _row_to_features(snap: OptionsSnapshot) -> dict[str, float]:
    has_opts = bool(snap.has_options)
    feats: dict[str, float] = {}
    for col, default in DEFAULT_FEATURES.items():
        if col == "has_options":
            feats[col] = 1.0 if has_opts else 0.0
            continue
        val = getattr(snap, col, None)
        feats[col] = float(val) if (val is not None and has_opts) else default
    return feats


def attach_options_features(db: Session, df: pd.DataFrame) -> pd.DataFrame:
    """Join options-snapshot features onto a DataFrame with ['ticker', 'date'] columns.

    Uses an as-of join: each (ticker, date) row is matched to the most recent
    snapshot at or before that date. Missing matches fall back to defaults.
    """
    if df.empty:
        for col, default in DEFAULT_FEATURES.items():
            df[col] = default
        return df

    tickers = df["ticker"].unique().tolist()
    snap_rows = (
        db.query(OptionsSnapshot)
        .filter(OptionsSnapshot.ticker.in_(tickers))
        .all()
    )

    snap_records: list[dict[str, Any]] = []
    for s in snap_rows:
        rec = _row_to_features(s)
        rec["ticker"] = s.ticker
        rec["date"] = s.date
        snap_records.append(rec)

    if not snap_records:
        for col, default in DEFAULT_FEATURES.items():
            df[col] = default
        return df

    snaps = pd.DataFrame(snap_records)
    snaps["date"] = pd.to_datetime(snaps["date"])
    snaps = snaps.sort_values("date").reset_index(drop=True)

    df_sorted = df.copy()
    df_sorted["_orig_date"] = df_sorted["date"]
    df_sorted["date"] = pd.to_datetime(df_sorted["date"])
    df_sorted = df_sorted.sort_values("date").reset_index(drop=True)

    merged = pd.merge_asof(
        df_sorted,
        snaps,
        by="ticker",
        on="date",
        direction="backward",
    )
    merged["date"] = merged["_orig_date"]
    merged = merged.drop(columns=["_orig_date"])

    # merge_asof leaves NaN where no snapshot existed at-or-before the row date
    for col, default in DEFAULT_FEATURES.items():
        if col not in merged.columns:
            merged[col] = default
        else:
            merged[col] = merged[col].fillna(default)
    return merged
