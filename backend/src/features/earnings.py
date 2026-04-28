"""Earnings-proximity features (P9-005).

Trades within ~10 days of earnings have very different return characteristics
because IV is elevated and gap risk dominates. The model needs to know the
proximity so it can either learn separate behavior, or have it filtered upstream.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy.orm import Session

from src.db.models import EarningsCalendar

EARNINGS_FEATURE_COLS = [
    "days_to_earnings",
    "days_since_earnings",
    "earnings_within_3d",
    "earnings_within_10d",
]

# -1 signals "unknown earnings date" — meaningfully different from "100 days out".
DEFAULT_EARNINGS_FEATURES: dict[str, float] = {
    "days_to_earnings": -1.0,
    "days_since_earnings": -1.0,
    "earnings_within_3d": 0.0,
    "earnings_within_10d": 0.0,
}

DAYS_TO_CAP = 90


def get_earnings_features(db: Session, ticker: str, on_date: date | None = None) -> dict[str, float]:
    on_date = on_date or date.today()
    rows = (
        db.query(EarningsCalendar.earnings_date)
        .filter(EarningsCalendar.ticker == ticker)
        .order_by(EarningsCalendar.earnings_date.asc())
        .all()
    )
    if not rows:
        return dict(DEFAULT_EARNINGS_FEATURES)

    earnings_dates = [r[0] for r in rows]
    upcoming = [d for d in earnings_dates if d >= on_date]
    past = [d for d in earnings_dates if d < on_date]

    days_to = (upcoming[0] - on_date).days if upcoming else -1
    if days_to > DAYS_TO_CAP:
        days_to = DAYS_TO_CAP
    days_since = (on_date - past[-1]).days if past else -1

    return {
        "days_to_earnings": float(days_to),
        "days_since_earnings": float(days_since),
        "earnings_within_3d": 1.0 if 0 <= days_to <= 3 else 0.0,
        "earnings_within_10d": 1.0 if 0 <= days_to <= 10 else 0.0,
    }


def attach_earnings_features(db: Session, df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        for col, default in DEFAULT_EARNINGS_FEATURES.items():
            df[col] = default
        return df

    out = df.copy()
    cache: dict[str, list[date]] = {}

    def _earnings_for(ticker: str) -> list[date]:
        if ticker not in cache:
            rows = (
                db.query(EarningsCalendar.earnings_date)
                .filter(EarningsCalendar.ticker == ticker)
                .order_by(EarningsCalendar.earnings_date.asc())
                .all()
            )
            cache[ticker] = [r[0] for r in rows]
        return cache[ticker]

    rows_out = []
    for _, row in out.iterrows():
        ticker = row["ticker"]
        on_date = row["date"]
        dates = _earnings_for(ticker)
        if not dates:
            rows_out.append(dict(DEFAULT_EARNINGS_FEATURES))
            continue
        upcoming = [d for d in dates if d >= on_date]
        past = [d for d in dates if d < on_date]
        days_to = (upcoming[0] - on_date).days if upcoming else -1
        if days_to > DAYS_TO_CAP:
            days_to = DAYS_TO_CAP
        days_since = (on_date - past[-1]).days if past else -1
        rows_out.append({
            "days_to_earnings": float(days_to),
            "days_since_earnings": float(days_since),
            "earnings_within_3d": 1.0 if 0 <= days_to <= 3 else 0.0,
            "earnings_within_10d": 1.0 if 0 <= days_to <= 10 else 0.0,
        })

    feats_df = pd.DataFrame(rows_out)
    for col in EARNINGS_FEATURE_COLS:
        out[col] = feats_df[col].values
    return out
