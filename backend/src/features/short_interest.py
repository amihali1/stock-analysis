"""Short interest features (P10-003).

Per-ticker event features built from `short_interest_snapshots`. Designed to add
information independent of price action — rising short interest and short
squeezes both produce price action that the technicals can't predict on their
own.

Each feature is a windowed aggregate over the FINRA bi-monthly settlement
report history preceding the as-of date. Defaults are sane "uninformative"
values for tickers without a snapshot yet, since the table accumulates over
time as the daily fetcher runs.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, pstdev

import pandas as pd
from sqlalchemy.orm import Session

from src.db.models import ShortInterestSnapshot

SHORT_INTEREST_FEATURE_COLS = [
    "short_percent_of_float",
    "short_ratio_days_to_cover",
    "short_interest_change_pct",
    "short_interest_zscore_180d",
    "days_since_short_report",
    "has_short_data",
]

# Sane "uninformative" defaults. Mean US large-cap short interest is ~2-4%
# of float; days-to-cover typically 1-3. has_short_data=0 lets the model
# distinguish "missing" from "low" if it learns to use that signal.
DEFAULT_SHORT_INTEREST_FEATURES: dict[str, float] = {
    "short_percent_of_float": 0.03,
    "short_ratio_days_to_cover": 2.0,
    "short_interest_change_pct": 0.0,
    "short_interest_zscore_180d": 0.0,
    "days_since_short_report": -1.0,  # sentinel: "no report ever observed"
    "has_short_data": 0.0,
}

DAYS_SINCE_CAP = 180  # FINRA cycles every ~15d; 180d cap is generous

# Rows used for the 180d z-score window. With FINRA's twice-monthly cadence,
# 180d ≈ 12 reports — enough for a stable mean/std.
ZSCORE_WINDOW_DAYS = 180


def _compute(snapshots: list[tuple[date, float, float, float]], on_date: date) -> dict[str, float]:
    """snapshots: sorted list of (report_date, shares_short, short_percent_of_float,
    short_ratio_days_to_cover) tuples for one ticker, oldest first."""
    past = [s for s in snapshots if s[0] <= on_date]
    if not past:
        return dict(DEFAULT_SHORT_INTEREST_FEATURES)

    most_recent = past[-1]
    report_date, shares_short, sp_float, days_to_cover = most_recent

    days_since = (on_date - report_date).days
    if days_since > DAYS_SINCE_CAP:
        days_since = DAYS_SINCE_CAP

    # Change vs prior snapshot
    if len(past) >= 2 and past[-2][1] and past[-2][1] > 0:
        change_pct = (shares_short - past[-2][1]) / past[-2][1]
    else:
        change_pct = 0.0

    # Z-score of current shares_short over trailing 180d
    cutoff = on_date - timedelta(days=ZSCORE_WINDOW_DAYS)
    window = [s[1] for s in past if s[0] >= cutoff and s[1] is not None]
    if len(window) >= 3:
        m = mean(window)
        sd = pstdev(window)
        zscore = (shares_short - m) / sd if abs(sd) > 1e-9 else 0.0
    else:
        zscore = 0.0

    return {
        "short_percent_of_float": float(sp_float) if sp_float is not None else DEFAULT_SHORT_INTEREST_FEATURES["short_percent_of_float"],
        "short_ratio_days_to_cover": float(days_to_cover) if days_to_cover is not None else DEFAULT_SHORT_INTEREST_FEATURES["short_ratio_days_to_cover"],
        "short_interest_change_pct": float(change_pct),
        "short_interest_zscore_180d": float(zscore),
        "days_since_short_report": float(days_since),
        "has_short_data": 1.0,
    }


def _load_snapshots(db: Session, ticker: str) -> list[tuple[date, float, float, float]]:
    rows = (
        db.query(
            ShortInterestSnapshot.report_date,
            ShortInterestSnapshot.shares_short,
            ShortInterestSnapshot.short_percent_of_float,
            ShortInterestSnapshot.short_ratio_days_to_cover,
        )
        .filter(ShortInterestSnapshot.ticker == ticker)
        .filter(ShortInterestSnapshot.has_data == 1)
        .order_by(ShortInterestSnapshot.report_date.asc())
        .all()
    )
    return [(d, ss, sp, dc) for d, ss, sp, dc in rows]


def get_short_interest_features(db: Session, ticker: str, on_date: date | None = None) -> dict[str, float]:
    on_date = on_date or date.today()
    snapshots = _load_snapshots(db, ticker)
    if not snapshots:
        return dict(DEFAULT_SHORT_INTEREST_FEATURES)
    return _compute(snapshots, on_date)


def attach_short_interest_features(db: Session, df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        for col, default in DEFAULT_SHORT_INTEREST_FEATURES.items():
            df[col] = default
        return df

    out = df.copy()
    cache: dict[str, list[tuple[date, float, float, float]]] = defaultdict(list)

    rows_out: list[dict[str, float]] = []
    for _, row in out.iterrows():
        ticker = row["ticker"]
        on_date = row["date"]
        if hasattr(on_date, "date"):
            on_date = on_date.date()
        if ticker not in cache:
            cache[ticker] = _load_snapshots(db, ticker)
        snapshots = cache[ticker]
        if not snapshots:
            rows_out.append(dict(DEFAULT_SHORT_INTEREST_FEATURES))
            continue
        rows_out.append(_compute(snapshots, on_date))

    feats_df = pd.DataFrame(rows_out)
    for col in SHORT_INTEREST_FEATURE_COLS:
        out[col] = feats_df[col].values
    return out
