"""Sector relative-strength features.

Each ticker is mapped to a sector ETF (XLK, XLF, etc.); we expose the ticker's
return *relative to its sector* and the sector's own return as features.
A ticker down 2% on a day its sector is down 4% is showing strength.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd
from sqlalchemy.orm import Session

from src.config import sector_etf_for
from src.db.models import PriceHistory

SECTOR_FEATURE_COLS = [
    "sector_return_5d",
    "sector_return_20d",
    "return_5d_vs_sector",
    "return_20d_vs_sector",
]

DEFAULT_SECTOR_FEATURES: dict[str, float] = {
    "sector_return_5d": 0.0,
    "sector_return_20d": 0.0,
    "return_5d_vs_sector": 0.0,
    "return_20d_vs_sector": 0.0,
}


def _load_returns(db: Session, ticker: str) -> pd.DataFrame:
    rows = (
        db.query(PriceHistory.date, PriceHistory.close)
        .filter(PriceHistory.ticker == ticker)
        .order_by(PriceHistory.date.asc())
        .all()
    )
    if not rows:
        return pd.DataFrame(columns=["date", "close", "ret_5d", "ret_20d"])
    df = pd.DataFrame(rows, columns=["date", "close"])
    df["close"] = df["close"].astype(float)
    df["ret_5d"] = df["close"].pct_change(5)
    df["ret_20d"] = df["close"].pct_change(20)
    return df[["date", "ret_5d", "ret_20d"]]


def _sector_frame(db: Session, sector_ticker: str) -> pd.DataFrame:
    s = _load_returns(db, sector_ticker).rename(columns={
        "ret_5d": "sector_return_5d",
        "ret_20d": "sector_return_20d",
    })
    return s


def get_sector_features(db: Session, ticker: str, on_date: date | None = None) -> dict[str, float]:
    """Return sector-relative features for one ticker at a given date."""
    sector = sector_etf_for(ticker)
    ticker_df = _load_returns(db, ticker)
    sector_df = _sector_frame(db, sector)

    if ticker_df.empty or sector_df.empty:
        return dict(DEFAULT_SECTOR_FEATURES)

    if on_date is not None:
        ticker_df = ticker_df[ticker_df["date"] <= on_date]
        sector_df = sector_df[sector_df["date"] <= on_date]
    if ticker_df.empty or sector_df.empty:
        return dict(DEFAULT_SECTOR_FEATURES)

    t = ticker_df.iloc[-1]
    # Match the sector row to the same date if available, else most recent ≤ date
    s = sector_df[sector_df["date"] <= t["date"]]
    if s.empty:
        return dict(DEFAULT_SECTOR_FEATURES)
    s = s.iloc[-1]

    return {
        "sector_return_5d": float(s["sector_return_5d"]) if pd.notna(s["sector_return_5d"]) else 0.0,
        "sector_return_20d": float(s["sector_return_20d"]) if pd.notna(s["sector_return_20d"]) else 0.0,
        "return_5d_vs_sector": float((t["ret_5d"] or 0) - (s["sector_return_5d"] or 0)) if pd.notna(t["ret_5d"]) and pd.notna(s["sector_return_5d"]) else 0.0,
        "return_20d_vs_sector": float((t["ret_20d"] or 0) - (s["sector_return_20d"] or 0)) if pd.notna(t["ret_20d"]) and pd.notna(s["sector_return_20d"]) else 0.0,
    }


def attach_sector_features(db: Session, df: pd.DataFrame) -> pd.DataFrame:
    """Attach sector features onto a per-(ticker, date) DataFrame."""
    if df.empty:
        for col, default in DEFAULT_SECTOR_FEATURES.items():
            df[col] = default
        return df

    out = df.copy()
    tickers: Iterable[str] = out["ticker"].unique()
    sector_ret = {}  # cache: sector_ticker → DataFrame

    pieces = []
    for ticker in tickers:
        sector = sector_etf_for(ticker)
        sec_df = sector_ret.setdefault(sector, _sector_frame(db, sector))
        ticker_df = _load_returns(db, ticker).rename(columns={
            "ret_5d": "_t_ret_5d",
            "ret_20d": "_t_ret_20d",
        })
        if ticker_df.empty:
            continue

        # Outer-merge so we keep ticker rows even when sector has no row that exact day
        merged = ticker_df.merge(sec_df, on="date", how="left").sort_values("date")
        merged["sector_return_5d"] = merged["sector_return_5d"].ffill()
        merged["sector_return_20d"] = merged["sector_return_20d"].ffill()
        merged["return_5d_vs_sector"] = merged["_t_ret_5d"] - merged["sector_return_5d"]
        merged["return_20d_vs_sector"] = merged["_t_ret_20d"] - merged["sector_return_20d"]
        merged["ticker"] = ticker
        pieces.append(merged[["ticker", "date", *SECTOR_FEATURE_COLS]])

    if not pieces:
        for col, default in DEFAULT_SECTOR_FEATURES.items():
            out[col] = default
        return out

    sector_features = pd.concat(pieces, ignore_index=True)
    merged_out = out.merge(sector_features, on=["ticker", "date"], how="left")
    for col, default in DEFAULT_SECTOR_FEATURES.items():
        if col not in merged_out.columns:
            merged_out[col] = default
        else:
            merged_out[col] = merged_out[col].fillna(default)
    return merged_out
