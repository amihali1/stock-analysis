"""Analyst rating-change features (P10-001).

Per-ticker event features built from the `analyst_ratings` table. Designed to add
information independent of price action — clustered downgrades and recent
rating cuts often precede price drops the technicals haven't caught yet.

Each feature is a windowed aggregate over the per-firm rating change history
preceding the as-of date, joined onto the directional model's training rows.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
from sqlalchemy.orm import Session

from src.db.models import AnalystRating

ANALYST_FEATURE_COLS = [
    "days_since_downgrade",
    "days_since_upgrade",
    "downgrades_30d",
    "upgrades_30d",
    "net_rating_actions_60d",
    "analyst_action_5d",
]

# -1 = "no rating action ever observed" — semantically distinct from "365 days ago".
DEFAULT_ANALYST_FEATURES: dict[str, float] = {
    "days_since_downgrade": -1.0,
    "days_since_upgrade": -1.0,
    "downgrades_30d": 0.0,
    "upgrades_30d": 0.0,
    "net_rating_actions_60d": 0.0,
    "analyst_action_5d": 0.0,
}

DAYS_SINCE_CAP = 365

DOWN_ACTIONS = {"down", "downgrade"}
UP_ACTIONS = {"up", "upgrade"}


def _compute(actions: list[tuple[date, str]], on_date: date) -> dict[str, float]:
    """actions is a sorted list of (date, action) tuples for one ticker."""
    past = [(d, a) for d, a in actions if d <= on_date]
    if not past:
        return dict(DEFAULT_ANALYST_FEATURES)

    last_down = next((d for d, a in reversed(past) if a in DOWN_ACTIONS), None)
    last_up = next((d for d, a in reversed(past) if a in UP_ACTIONS), None)

    days_since_down = (on_date - last_down).days if last_down else -1
    days_since_up = (on_date - last_up).days if last_up else -1
    if days_since_down > DAYS_SINCE_CAP:
        days_since_down = DAYS_SINCE_CAP
    if days_since_up > DAYS_SINCE_CAP:
        days_since_up = DAYS_SINCE_CAP

    cutoff_30 = on_date - timedelta(days=30)
    cutoff_60 = on_date - timedelta(days=60)
    cutoff_5 = on_date - timedelta(days=5)

    downgrades_30 = sum(1 for d, a in past if d >= cutoff_30 and a in DOWN_ACTIONS)
    upgrades_30 = sum(1 for d, a in past if d >= cutoff_30 and a in UP_ACTIONS)
    downgrades_60 = sum(1 for d, a in past if d >= cutoff_60 and a in DOWN_ACTIONS)
    upgrades_60 = sum(1 for d, a in past if d >= cutoff_60 and a in UP_ACTIONS)
    any_5d = 1.0 if any(d >= cutoff_5 for d, _ in past) else 0.0

    return {
        "days_since_downgrade": float(days_since_down),
        "days_since_upgrade": float(days_since_up),
        "downgrades_30d": float(downgrades_30),
        "upgrades_30d": float(upgrades_30),
        "net_rating_actions_60d": float(upgrades_60 - downgrades_60),
        "analyst_action_5d": any_5d,
    }


def _load_actions(db: Session, ticker: str) -> list[tuple[date, str]]:
    rows = (
        db.query(AnalystRating.date, AnalystRating.action)
        .filter(AnalystRating.ticker == ticker)
        .order_by(AnalystRating.date.asc())
        .all()
    )
    return [(d, (a or "").lower()) for d, a in rows]


def get_analyst_features(db: Session, ticker: str, on_date: date | None = None) -> dict[str, float]:
    on_date = on_date or date.today()
    actions = _load_actions(db, ticker)
    if not actions:
        return dict(DEFAULT_ANALYST_FEATURES)
    return _compute(actions, on_date)


def attach_analyst_features(db: Session, df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        for col, default in DEFAULT_ANALYST_FEATURES.items():
            df[col] = default
        return df

    out = df.copy()
    cache: dict[str, list[tuple[date, str]]] = defaultdict(list)

    rows_out: list[dict[str, float]] = []
    for _, row in out.iterrows():
        ticker = row["ticker"]
        on_date = row["date"]
        if ticker not in cache:
            cache[ticker] = _load_actions(db, ticker)
        actions = cache[ticker]
        if not actions:
            rows_out.append(dict(DEFAULT_ANALYST_FEATURES))
            continue
        rows_out.append(_compute(actions, on_date))

    feats_df = pd.DataFrame(rows_out)
    for col in ANALYST_FEATURE_COLS:
        out[col] = feats_df[col].values
    return out
