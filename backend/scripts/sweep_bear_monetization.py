"""Bear-monetization sweep: can bear picks make ABSOLUTE money?

The money-layer sweep (sweep_money_layer.py, 2026-07-07) showed bear picks
carry real RELATIVE alpha — their underlyings rose +0.14%/10d vs +0.68%
universe — but naked shorts lose absolutely in a rising tape. This sweep
re-simulates the SAME walk-forward bear picks under structures that monetize
"rises less than X" instead of "falls":

  1. naked_short  — baseline, known slightly negative
  2. pair         — short pick + equal-dollar long SPY (market-neutral spread);
                    return reported per dollar deployed across both legs
  3. credit_spread— bear call credit spread: short strike entry*(1 + d*volH),
                    long strike one volH further out, Black-Scholes credit at
                    entry (IV≈HV assumption, r=4%), payoff at H-day close,
                    return on collateral (width - credit)
  4. regime split — every structure also reported for SPY>50dSMA (up-tape) vs
                    SPY<=50dSMA (down-tape) entry days

Grid: K {3,5,10} x H {5,10} x spread short-strike distance d {0.5,1.0,1.5} volH.
Output: trained_models/sweep_bear_monetization.json

Usage (inside backend container):
    python scripts/sweep_bear_monetization.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.db.models import PriceHistory  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402
from src.models.directional import (  # noqa: E402
    DEFAULT_DROP_VOL_K,
    FEATURE_COLS,
    LABEL_MODE_EXCESS,
    LABEL_MODE_VOL_NORMALIZED,
    _resolve_model_dir,
    build_dataset,
)
from run_joint_topk_backtest import _build_candidates_for_date, _fit_xgb  # noqa: E402
from src.pipeline.rec_ranker import select_candidates  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bear-monetization")

N_FOLDS = 4
KS = [3, 5, 10]
HOLDS = [5, 10]
STRIKE_DISTS = [0.5, 1.0, 1.5]  # short-strike distance in volH units
RISK_FREE = 0.04


def _bs_call(S: float, K: float, T: float, sigma: float, r: float = RISK_FREE) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, S - K)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return float(S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2))


def _load_paths() -> dict[str, pd.DataFrame]:
    db = SessionLocal()
    try:
        rows = db.query(
            PriceHistory.ticker, PriceHistory.date, PriceHistory.close,
        ).filter(PriceHistory.close.isnot(None)).all()
    finally:
        db.close()
    df = pd.DataFrame(rows, columns=["ticker", "date", "close"])
    out: dict[str, pd.DataFrame] = {}
    for ticker, g in df.groupby("ticker"):
        out[ticker] = g.sort_values("date").set_index("date")
    return out


def _merged_dataset() -> pd.DataFrame:
    df_drop = build_dataset(
        direction="drop", label_mode=LABEL_MODE_VOL_NORMALIZED,
        feature_cols=FEATURE_COLS, vol_k=DEFAULT_DROP_VOL_K,
    )
    df_rise = build_dataset(
        direction="rise", label_mode=LABEL_MODE_EXCESS, feature_cols=FEATURE_COLS,
    )
    df = df_drop.rename(columns={"label": "label_drop"}).merge(
        df_rise[["ticker", "date", "label"]].rename(columns={"label": "label_rise"}),
        on=["ticker", "date"], how="inner",
    )
    return df.sort_values("date").reset_index(drop=True)


def _exit_close(path: pd.DataFrame, entry_date, hold: int):
    """(entry_close, exit_close) at t and t+hold sessions, or None."""
    try:
        loc = path.index.get_loc(entry_date)
    except KeyError:
        return None
    if loc + hold >= len(path):
        return None
    entry = float(path.iloc[loc]["close"])
    exit_ = float(path.iloc[loc + hold]["close"])
    if entry <= 0 or not np.isfinite(entry) or not np.isfinite(exit_):
        return None
    return entry, exit_


def _summ(rets: list[float]) -> dict | None:
    if not rets:
        return None
    arr = np.array(rets)
    return {
        "n": len(arr),
        "expectancy": round(float(arr.mean()), 5),
        "std": round(float(arr.std()), 5),
        "win_rate": round(float((arr > 0).mean()), 4),
        "total": round(float(arr.sum()), 3),
    }


def main() -> int:
    logger.info("Building merged dataset...")
    df = _merged_dataset()
    paths = _load_paths()
    spy = paths["SPY"].copy()
    spy["sma50"] = spy["close"].rolling(50).mean()
    logger.info("Merged: %d rows; paths: %d tickers", len(df), len(paths))

    dates = sorted(df["date"].unique())
    fold_size = len(dates) // (N_FOLDS + 1)

    bear_picks: dict[int, list[dict]] = {k: [] for k in KS}
    for fold in range(N_FOLDS):
        train = df[df["date"].isin(set(dates[: (fold + 1) * fold_size]))]
        test = df[df["date"].isin(set(dates[(fold + 1) * fold_size: (fold + 2) * fold_size]))].copy()
        logger.info("Fold %d: train=%d test=%d", fold + 1, len(train), len(test))
        drop_model = _fit_xgb(train[FEATURE_COLS], train["label_drop"], dates=train["date"], calibrate=True)
        rise_model = _fit_xgb(train[FEATURE_COLS], train["label_rise"], dates=train["date"], calibrate=True)
        test["drop_prob"] = drop_model.predict_proba(test[FEATURE_COLS])[:, 1]
        test["rise_prob"] = rise_model.predict_proba(test[FEATURE_COLS])[:, 1]

        for d, day_slice in test.groupby("date"):
            candidates = _build_candidates_for_date(day_slice)
            for k in KS:
                for c in select_candidates(candidates, top_k=k, min_score=None):
                    if c.direction != "drop":
                        continue
                    row = day_slice.loc[c.extras["row_index"]]
                    vol_ann = float(row["volatility_20d"])
                    if not np.isfinite(vol_ann) or vol_ann <= 0:
                        continue
                    bear_picks[k].append({"ticker": c.ticker, "date": d, "vol_ann": vol_ann})

    logger.info("Bear picks: %s", {k: len(v) for k, v in bear_picks.items()})

    results = []
    for k in KS:
        for hold in HOLDS:
            T = hold / 252.0
            buckets: dict[tuple, list[float]] = {}
            for tr in bear_picks[k]:
                path = paths.get(tr["ticker"])
                if path is None:
                    continue
                sim = _exit_close(path, tr["date"], hold)
                spy_sim = _exit_close(spy, tr["date"], hold)
                if sim is None or spy_sim is None:
                    continue
                entry, exit_ = sim
                spy_entry, spy_exit = spy_sim

                sma = spy.loc[tr["date"], "sma50"] if tr["date"] in spy.index else np.nan
                regime = "up" if (np.isfinite(sma) and spy_entry > sma) else "down"

                short_ret = (entry - exit_) / entry
                spy_ret = (spy_exit - spy_entry) / spy_entry
                pair_ret = (short_ret + spy_ret) / 2.0

                for reg in ("all", regime):
                    buckets.setdefault(("naked_short", None, reg), []).append(short_ret)
                    buckets.setdefault(("pair", None, reg), []).append(pair_ret)

                vol_h = tr["vol_ann"] / np.sqrt(252.0 / hold)
                sigma = tr["vol_ann"]
                for dist in STRIKE_DISTS:
                    k_short = entry * (1 + dist * vol_h)
                    k_long = entry * (1 + (dist + 1.0) * vol_h)
                    credit = _bs_call(entry, k_short, T, sigma) - _bs_call(entry, k_long, T, sigma)
                    width = k_long - k_short
                    collateral = width - credit
                    if collateral <= 0 or credit <= 0:
                        continue
                    payoff = max(exit_ - k_short, 0.0) - max(exit_ - k_long, 0.0)
                    ror = (credit - payoff) / collateral  # return on collateral
                    for reg in ("all", regime):
                        buckets.setdefault(("credit_spread", dist, reg), []).append(ror)

            for (structure, dist, reg), rets in sorted(buckets.items()):
                s = _summ(rets)
                if s:
                    results.append({"k": k, "hold": hold, "structure": structure,
                                    "strike_dist": dist, "regime": reg, **s})

    out_path = _resolve_model_dir() / "sweep_bear_monetization.json"
    out_path.write_text(json.dumps({"configs": results}, indent=2))
    logger.info("Wrote %s", out_path)

    logger.info("=== ALL-REGIME RESULTS ===")
    for r in sorted([r for r in results if r["regime"] == "all"],
                    key=lambda x: x["expectancy"], reverse=True):
        logger.info("  K=%d H=%d %s(d=%s): exp=%+.4f win=%.2f n=%d total=%+.1f",
                    r["k"], r["hold"], r["structure"], r["strike_dist"],
                    r["expectancy"], r["win_rate"], r["n"], r["total"])
    logger.info("=== DOWN-REGIME ONLY ===")
    for r in sorted([r for r in results if r["regime"] == "down"],
                    key=lambda x: x["expectancy"], reverse=True)[:12]:
        logger.info("  K=%d H=%d %s(d=%s): exp=%+.4f win=%.2f n=%d",
                    r["k"], r["hold"], r["structure"], r["strike_dist"],
                    r["expectancy"], r["win_rate"], r["n"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
