"""Insider-transaction features (P10-005).

Per-ticker event features built from the `insider_transactions` table. The
signal hypothesis (Cohen, Malloy & Pomorski 2012; Lakonishok & Lee 2001):
clustered insider buying is among the strongest free per-ticker bullish
signals, and the buy/sell mix swings well ahead of price action on quiet
news days.

`shares` in the source table is signed (acquired = +, disposed = −) by the
fetcher's convention, but the directional signal lives in the transaction
*code*, not the share sign — so feature aggregation filters by code rather
than re-deriving sign. We only count P (open-market purchase) and S
(open-market sale); grants (A), gifts (G), tax events (F, M), and other
non-discretionary codes are filtered out — they don't carry directional
intent and counting them as "selling" is a known false-signal source.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from sqlalchemy.orm import Session

from src.db.models import InsiderTransaction

INSIDER_FEATURE_COLS = [
    "insider_buys_30d",
    "insider_sells_30d",
    "insider_net_buy_value_30d",
    "insider_net_buy_value_90d",
    "insider_cluster_buy_30d",
    "insider_buy_sell_ratio_90d",
    "days_since_insider_buy",
    "days_since_insider_sell",
    "pct_insider_ownership_change_90d",
]

# -1 = "no insider activity ever observed in our window" — semantically
# distinct from "365 days ago" or "0 days ago", so we keep a sentinel rather
# than letting XGBoost confuse it with very-old activity.
DEFAULT_INSIDER_FEATURES: dict[str, float] = {
    "insider_buys_30d": 0.0,
    "insider_sells_30d": 0.0,
    "insider_net_buy_value_30d": 0.0,
    "insider_net_buy_value_90d": 0.0,
    "insider_cluster_buy_30d": 0.0,
    "insider_buy_sell_ratio_90d": 0.5,  # neutral midpoint
    "days_since_insider_buy": -1.0,
    "days_since_insider_sell": -1.0,
    "pct_insider_ownership_change_90d": 0.0,
}

DAYS_SINCE_CAP = 365
CLUSTER_BUY_INSIDER_THRESHOLD = 3  # ≥3 distinct insiders in window

BUY_CODE = "P"
SELL_CODE = "S"


@dataclass
class _Tx:
    transaction_date: date
    code: str | None
    shares: float
    price_per_share: float | None
    total_value: float | None
    insider_name: str | None
    shares_owned_after: float | None


def _load_transactions(db: Session, ticker: str) -> list[_Tx]:
    rows = (
        db.query(
            InsiderTransaction.transaction_date,
            InsiderTransaction.transaction_code,
            InsiderTransaction.shares,
            InsiderTransaction.price_per_share,
            InsiderTransaction.total_value,
            InsiderTransaction.insider_name,
            InsiderTransaction.shares_owned_after,
        )
        .filter(InsiderTransaction.ticker == ticker)
        .order_by(InsiderTransaction.transaction_date.asc())
        .all()
    )
    return [
        _Tx(
            transaction_date=r[0],
            code=r[1],
            shares=float(r[2] or 0.0),
            price_per_share=(float(r[3]) if r[3] is not None else None),
            total_value=(float(r[4]) if r[4] is not None else None),
            insider_name=r[5],
            shares_owned_after=(float(r[6]) if r[6] is not None else None),
        )
        for r in rows
    ]


def _abs_value(tx: _Tx) -> float:
    """Dollar value of the transaction, falling back to |shares|*price."""
    if tx.total_value is not None:
        return abs(tx.total_value)
    if tx.price_per_share is not None:
        return abs(tx.shares) * tx.price_per_share
    return 0.0


def _compute(transactions: list[_Tx], on_date: date) -> dict[str, float]:
    past = [t for t in transactions if t.transaction_date <= on_date]
    if not past:
        return dict(DEFAULT_INSIDER_FEATURES)

    cutoff_30 = on_date - timedelta(days=30)
    cutoff_90 = on_date - timedelta(days=90)

    buys_30 = [t for t in past if t.code == BUY_CODE and t.transaction_date >= cutoff_30]
    sells_30 = [t for t in past if t.code == SELL_CODE and t.transaction_date >= cutoff_30]
    buys_90 = [t for t in past if t.code == BUY_CODE and t.transaction_date >= cutoff_90]
    sells_90 = [t for t in past if t.code == SELL_CODE and t.transaction_date >= cutoff_90]

    buy_value_30 = sum(_abs_value(t) for t in buys_30)
    sell_value_30 = sum(_abs_value(t) for t in sells_30)
    buy_value_90 = sum(_abs_value(t) for t in buys_90)
    sell_value_90 = sum(_abs_value(t) for t in sells_90)

    distinct_buyers_30 = {t.insider_name for t in buys_30 if t.insider_name}
    cluster_buy = 1.0 if len(distinct_buyers_30) >= CLUSTER_BUY_INSIDER_THRESHOLD else 0.0

    buys_90_count = len(buys_90)
    sells_90_count = len(sells_90)
    denom = buys_90_count + sells_90_count
    buy_sell_ratio_90 = (
        buys_90_count / denom if denom > 0 else DEFAULT_INSIDER_FEATURES["insider_buy_sell_ratio_90d"]
    )

    last_buy = next(
        (t.transaction_date for t in reversed(past) if t.code == BUY_CODE), None
    )
    last_sell = next(
        (t.transaction_date for t in reversed(past) if t.code == SELL_CODE), None
    )

    days_since_buy = (on_date - last_buy).days if last_buy else -1
    days_since_sell = (on_date - last_sell).days if last_sell else -1
    if days_since_buy > DAYS_SINCE_CAP:
        days_since_buy = DAYS_SINCE_CAP
    if days_since_sell > DAYS_SINCE_CAP:
        days_since_sell = DAYS_SINCE_CAP

    pct_ownership_change_90 = _ownership_change_pct(past, cutoff_90, on_date)

    return {
        "insider_buys_30d": float(len(buys_30)),
        "insider_sells_30d": float(len(sells_30)),
        "insider_net_buy_value_30d": float(buy_value_30 - sell_value_30),
        "insider_net_buy_value_90d": float(buy_value_90 - sell_value_90),
        "insider_cluster_buy_30d": cluster_buy,
        "insider_buy_sell_ratio_90d": float(buy_sell_ratio_90),
        "days_since_insider_buy": float(days_since_buy),
        "days_since_insider_sell": float(days_since_sell),
        "pct_insider_ownership_change_90d": float(pct_ownership_change_90),
    }


def _ownership_change_pct(
    past: list[_Tx], cutoff_90: date, on_date: date
) -> float:
    """% change in aggregate `shares_owned_after` between earliest 90d-window
    insider's pre-window balance and the most recent 90d-window balance.

    This is intentionally noisy at the per-insider level — we want a *cohort*
    signal, so we sum `shares_owned_after` across insiders' most recent
    pre-window and most recent in-window observations.
    """
    in_window = [t for t in past if cutoff_90 <= t.transaction_date <= on_date]
    if not in_window:
        return 0.0

    # For each insider with activity in the 90d window, find their most recent
    # balance *before* the window (baseline) and most recent balance *in* the
    # window (current). If there's no prior baseline observation, skip them —
    # we have no zero-point to measure from.
    by_insider_in: dict[str, list[_Tx]] = defaultdict(list)
    for t in in_window:
        if t.insider_name and t.shares_owned_after is not None:
            by_insider_in[t.insider_name].append(t)

    baseline_total = 0.0
    current_total = 0.0
    saw_any = False
    for insider, in_txs in by_insider_in.items():
        prior = [
            t for t in past
            if t.insider_name == insider
            and t.transaction_date < cutoff_90
            and t.shares_owned_after is not None
        ]
        if not prior:
            continue
        baseline_total += prior[-1].shares_owned_after  # most recent prior
        current_total += in_txs[-1].shares_owned_after  # most recent in-window
        saw_any = True

    if not saw_any or baseline_total <= 0:
        return 0.0
    return (current_total - baseline_total) / baseline_total * 100.0


def get_insider_features(
    db: Session, ticker: str, on_date: date | None = None
) -> dict[str, float]:
    on_date = on_date or date.today()
    transactions = _load_transactions(db, ticker)
    if not transactions:
        return dict(DEFAULT_INSIDER_FEATURES)
    return _compute(transactions, on_date)


def attach_insider_features(db: Session, df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        for col, default in DEFAULT_INSIDER_FEATURES.items():
            df[col] = default
        return df

    out = df.copy()
    cache: dict[str, list[_Tx]] = {}

    rows_out: list[dict[str, float]] = []
    for _, row in out.iterrows():
        ticker = row["ticker"]
        on_date = row["date"]
        if ticker not in cache:
            cache[ticker] = _load_transactions(db, ticker)
        transactions = cache[ticker]
        if not transactions:
            rows_out.append(dict(DEFAULT_INSIDER_FEATURES))
            continue
        rows_out.append(_compute(transactions, on_date))

    feats_df = pd.DataFrame(rows_out)
    for col in INSIDER_FEATURE_COLS:
        out[col] = feats_df[col].values
    return out
