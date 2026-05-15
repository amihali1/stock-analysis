"""SEC 8-K event features (P10-009).

Per-ticker catalyst-event features built from the `sec_filings_8k` table.
Drop-side AUC has been stuck around 0.55 because the model has no view on
"did something material just happen at this company"; 8-K filings are the
canonical structured record of those events (acquisitions, officer changes,
restated financials, triggering events).

We deliberately keep features count-based rather than try to encode item
codes individually — XGBoost can learn the gross "any material event in
last 30d?" signal without the dimensionality explosion of one-hot per item.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy.orm import Session

from src.db.models import SECFiling8K

SEC_8K_FEATURE_COLS = [
    "days_since_8k",
    "days_since_material_8k",
    "count_8k_30d",
    "count_8k_90d",
    "count_material_8k_30d",
]

DAYS_SINCE_CAP = 365

# Item codes treated as "material" — these tend to move price even on
# otherwise quiet news days. 2.02 (earnings PR) and 7.01 (Reg FD), 8.01
# (Other) are deliberately NOT material here: earnings is already handled by
# the dedicated earnings feature set, and 7.01/8.01 are too noisy to single
# out.
MATERIAL_ITEMS: frozenset[str] = frozenset({
    "1.01",  # entry into material agreement
    "1.02",  # termination of material agreement
    "2.01",  # acquisition / disposition completion
    "2.04",  # triggering event
    "2.05",  # exit / disposal costs
    "3.01",  # delisting notice
    "4.02",  # non-reliance on prior financial statements
    "5.02",  # officer / director departure or appointment
})

# Defaults for tickers with zero 8-K history (or before earliest filing).
# DAYS_SINCE_CAP for the days-since features keeps them distinguishable
# from "filed today" (0) without injecting a sentinel value the model has
# to special-case.
DEFAULT_SEC_8K_FEATURES: dict[str, float] = {
    "days_since_8k": float(DAYS_SINCE_CAP),
    "days_since_material_8k": float(DAYS_SINCE_CAP),
    "count_8k_30d": 0.0,
    "count_8k_90d": 0.0,
    "count_material_8k_30d": 0.0,
}


def is_material(items_str: str | None) -> bool:
    """Return True if any item code in `items_str` is in MATERIAL_ITEMS."""
    if not items_str:
        return False
    for raw in items_str.split(","):
        if raw.strip() in MATERIAL_ITEMS:
            return True
    return False


def _load_filings(db: Session, ticker: str) -> list[tuple[date, bool]]:
    rows = (
        db.query(SECFiling8K.filing_date, SECFiling8K.is_material)
        .filter(SECFiling8K.ticker == ticker)
        .order_by(SECFiling8K.filing_date.asc())
        .all()
    )
    return [(r[0], bool(r[1])) for r in rows]


def _compute(filings: list[tuple[date, bool]], on_date: date) -> dict[str, float]:
    past = [f for f in filings if f[0] <= on_date]
    if not past:
        return dict(DEFAULT_SEC_8K_FEATURES)

    cutoff_30 = on_date - timedelta(days=30)
    cutoff_90 = on_date - timedelta(days=90)

    in_30 = [f for f in past if f[0] >= cutoff_30]
    in_90 = [f for f in past if f[0] >= cutoff_90]
    material_30 = [f for f in in_30 if f[1]]

    last_8k = past[-1][0]
    last_material = next((f[0] for f in reversed(past) if f[1]), None)

    days_since_8k = min((on_date - last_8k).days, DAYS_SINCE_CAP)
    days_since_material = (
        min((on_date - last_material).days, DAYS_SINCE_CAP)
        if last_material is not None else DAYS_SINCE_CAP
    )

    return {
        "days_since_8k": float(days_since_8k),
        "days_since_material_8k": float(days_since_material),
        "count_8k_30d": float(len(in_30)),
        "count_8k_90d": float(len(in_90)),
        "count_material_8k_30d": float(len(material_30)),
    }


def get_sec_8k_features(
    db: Session, ticker: str, on_date: date | None = None
) -> dict[str, float]:
    on_date = on_date or date.today()
    filings = _load_filings(db, ticker)
    if not filings:
        return dict(DEFAULT_SEC_8K_FEATURES)
    return _compute(filings, on_date)


def attach_sec_8k_features(db: Session, df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        for col, default in DEFAULT_SEC_8K_FEATURES.items():
            df[col] = default
        return df

    out = df.copy()
    cache: dict[str, list[tuple[date, bool]]] = {}

    rows_out: list[dict[str, float]] = []
    for _, row in out.iterrows():
        ticker = row["ticker"]
        on_date = row["date"]
        if ticker not in cache:
            cache[ticker] = _load_filings(db, ticker)
        filings = cache[ticker]
        if not filings:
            rows_out.append(dict(DEFAULT_SEC_8K_FEATURES))
            continue
        rows_out.append(_compute(filings, on_date))

    feats_df = pd.DataFrame(rows_out)
    for col in SEC_8K_FEATURE_COLS:
        out[col] = feats_df[col].values
    return out
