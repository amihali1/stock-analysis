"""Money-layer sweep: exit geometry x hold x K on REAL price paths.

The joint top-K backtest scores trades with a fixed +1.0/-1.5 payoff, which
bakes in a losing risk:reward at any hit rate below 60%. This sweep asks the
opposite question: holding signal quality fixed (AUC ~0.60-0.64), is there an
exit geometry (stop/target in vol units), hold period, and selectivity K
where the SAME picks make money on actual daily high/low/close paths?

Protocol:
- Merged dataset: drop = vol_normalized K=1.75 (prod v7 semantics), rise =
  excess-vs-SPY (prod v2 semantics), production FEATURE_COLS.
- 4 expanding walk-forward folds; per fold train calibrated drop + rise XGB
  (same hyperparams as production) and predict the test slice.
- Per test date: per-direction top-K via prod select_candidates.
- Per selected trade x geometry config: entry = close(t); walk daily
  high/low t+1..t+H; stop checked BEFORE target each day (pessimistic);
  untouched -> exit at close(t+H). Returns are per-trade percentage moves;
  no slippage/commissions (paper Alpaca is commission-free; slippage noted
  as a caveat on spreads).
- Baseline config per (K, H): no stops/targets, exit at close(t+H).

Grid: K {3,5,10} x H {3,5,10} x stop {0.75,1.0,1.5} x target {1.0,1.5,2.0,3.0}
(vol-unit multiples of the H-day sigma), plus 9 no-exit baselines.

Output: trained_models/sweep_money_layer.json — every config with n, hit/stop
rates, expectancy (mean per-trade return), std, total, per-direction split.

Usage (inside backend container):
    python scripts/sweep_money_layer.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

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
logger = logging.getLogger("money-layer")

N_FOLDS = 4
KS = [3, 5, 10]
HOLDS = [3, 5, 10]
STOP_MULTS = [0.75, 1.0, 1.5]
TARGET_MULTS = [1.0, 1.5, 2.0, 3.0]
MIN_TRADES = 200  # configs below this are reported but flagged low-n


def _load_paths() -> dict[str, pd.DataFrame]:
    """ticker -> DataFrame(date-indexed) with high/low/close, sorted."""
    db = SessionLocal()
    try:
        rows = db.query(
            PriceHistory.ticker, PriceHistory.date,
            PriceHistory.high, PriceHistory.low, PriceHistory.close,
        ).filter(PriceHistory.close.isnot(None)).all()
    finally:
        db.close()
    df = pd.DataFrame(rows, columns=["ticker", "date", "high", "low", "close"])
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
    if df.empty:
        raise ValueError("drop/rise datasets did not align")
    return df.sort_values("date").reset_index(drop=True)


def _simulate(path: pd.DataFrame, entry_date, direction: str, vol_h: float,
              hold: int, stop_mult: float | None, target_mult: float | None):
    """Walk the daily path. Returns (trade_return, outcome) or None if the
    path is too short. vol_h is the H-day sigma as a FRACTION of price."""
    try:
        loc = path.index.get_loc(entry_date)
    except KeyError:
        return None
    if loc + 1 >= len(path):
        return None
    entry = float(path.iloc[loc]["close"])
    if entry <= 0 or not np.isfinite(entry):
        return None

    window = path.iloc[loc + 1: loc + 1 + hold]
    if window.empty:
        return None

    short = direction == "drop"
    stop_price = entry * (1 + stop_mult * vol_h) if (short and stop_mult) else \
                 entry * (1 - stop_mult * vol_h) if stop_mult else None
    target_price = entry * (1 - target_mult * vol_h) if (short and target_mult) else \
                   entry * (1 + target_mult * vol_h) if target_mult else None

    exit_price, outcome = None, "expiry"
    for _, day in window.iterrows():
        hi, lo = float(day["high"]), float(day["low"])
        # Pessimistic: adverse touch checked first within the day.
        if stop_price is not None and ((short and hi >= stop_price) or (not short and lo <= stop_price)):
            exit_price, outcome = stop_price, "stop"
            break
        if target_price is not None and ((short and lo <= target_price) or (not short and hi >= target_price)):
            exit_price, outcome = target_price, "target"
            break
    if exit_price is None:
        exit_price = float(window.iloc[-1]["close"])

    ret = (entry - exit_price) / entry if short else (exit_price - entry) / entry
    return ret, outcome


def main() -> int:
    logger.info("Building merged dataset...")
    df = _merged_dataset()
    logger.info("Merged: %d rows, %d tickers", len(df), df["ticker"].nunique())
    paths = _load_paths()
    logger.info("Loaded paths for %d tickers", len(paths))

    # SPY 50d-SMA regime for a per-direction regime split (mirrors the bear
    # monetization sweep, which found down-tape pairs materially stronger).
    # The rise/money-layer side never had this breakdown — it's the evidence a
    # regime filter needs before gating either direction. up = SPY above its
    # 50d SMA at entry, else down.
    spy = paths.get("SPY")
    if spy is None:
        raise ValueError("SPY path required for regime split")
    spy = spy.copy()
    spy["sma50"] = spy["close"].rolling(50).mean()

    def _regime(entry_date) -> str:
        if entry_date not in spy.index:
            return "unknown"
        sma = spy.loc[entry_date, "sma50"]
        if not np.isfinite(sma):
            return "unknown"
        return "up" if spy.loc[entry_date, "close"] > sma else "down"

    dates = sorted(df["date"].unique())
    fold_size = len(dates) // (N_FOLDS + 1)

    # Collect selected trades once per K; geometry applied afterwards.
    selected: dict[int, list[dict]] = {k: [] for k in KS}

    for fold in range(N_FOLDS):
        train_dates = set(dates[: (fold + 1) * fold_size])
        test_dates = dates[(fold + 1) * fold_size: (fold + 2) * fold_size]
        train = df[df["date"].isin(train_dates)]
        test = df[df["date"].isin(set(test_dates))]
        logger.info("Fold %d: train=%d rows, test=%d rows (%s..%s)",
                    fold + 1, len(train), len(test), test_dates[0], test_dates[-1])

        drop_model = _fit_xgb(train[FEATURE_COLS], train["label_drop"],
                              dates=train["date"], calibrate=True)
        rise_model = _fit_xgb(train[FEATURE_COLS], train["label_rise"],
                              dates=train["date"], calibrate=True)

        test = test.copy()
        test["drop_prob"] = drop_model.predict_proba(test[FEATURE_COLS])[:, 1]
        test["rise_prob"] = rise_model.predict_proba(test[FEATURE_COLS])[:, 1]

        for d, day_slice in test.groupby("date"):
            candidates = _build_candidates_for_date(day_slice)
            for k in KS:
                for c in select_candidates(candidates, top_k=k, min_score=None):
                    row = day_slice.loc[c.extras["row_index"]]
                    vol_ann = float(row["volatility_20d"])
                    if not np.isfinite(vol_ann) or vol_ann <= 0:
                        continue
                    selected[k].append({
                        "ticker": c.ticker, "date": d, "direction": c.direction,
                        "vol_ann": vol_ann, "regime": _regime(d),
                    })

    logger.info("Selected trades: %s", {k: len(v) for k, v in selected.items()})

    results = []
    for k in KS:
        trades = selected[k]
        for hold in HOLDS:
            geoms = [(s, t) for s in STOP_MULTS for t in TARGET_MULTS] + [(None, None)]
            for stop_mult, target_mult in geoms:
                rets, outcomes, by_dir = [], [], {"drop": [], "rise": []}
                for tr in trades:
                    path = paths.get(tr["ticker"])
                    if path is None:
                        continue
                    vol_h = tr["vol_ann"] / np.sqrt(252.0 / hold)
                    sim = _simulate(path, tr["date"], tr["direction"], vol_h,
                                    hold, stop_mult, target_mult)
                    if sim is None:
                        continue
                    ret, outcome = sim
                    rets.append(ret)
                    outcomes.append(outcome)
                    by_dir[tr["direction"]].append(ret)
                if not rets:
                    continue
                arr = np.array(rets)
                results.append({
                    "k": k, "hold": hold,
                    "stop_mult": stop_mult, "target_mult": target_mult,
                    "n": len(arr),
                    "expectancy": round(float(arr.mean()), 5),
                    "std": round(float(arr.std()), 5),
                    "total_return_sum": round(float(arr.sum()), 3),
                    "target_rate": round(outcomes.count("target") / len(arr), 4),
                    "stop_rate": round(outcomes.count("stop") / len(arr), 4),
                    "expectancy_drop": round(float(np.mean(by_dir["drop"])), 5) if by_dir["drop"] else None,
                    "expectancy_rise": round(float(np.mean(by_dir["rise"])), 5) if by_dir["rise"] else None,
                    "low_n": len(arr) < MIN_TRADES,
                })

    results.sort(key=lambda r: r["expectancy"], reverse=True)
    out = {
        "grid": {"ks": KS, "holds": HOLDS, "stops": STOP_MULTS, "targets": TARGET_MULTS},
        "n_configs": len(results),
        "configs": results,
    }
    out_path = _resolve_model_dir() / "sweep_money_layer.json"
    out_path.write_text(json.dumps(out, indent=2))
    logger.info("Wrote %s", out_path)

    logger.info("TOP 10 by expectancy:")
    for r in results[:10]:
        logger.info("  K=%d H=%d stop=%s tgt=%s -> exp=%+.4f (n=%d, tgt%%=%.2f, stop%%=%.2f, drop=%s rise=%s)",
                    r["k"], r["hold"], r["stop_mult"], r["target_mult"],
                    r["expectancy"], r["n"], r["target_rate"], r["stop_rate"],
                    r["expectancy_drop"], r["expectancy_rise"])
    baselines = [r for r in results if r["stop_mult"] is None]
    logger.info("BASELINES (no exits):")
    for r in sorted(baselines, key=lambda x: (x["k"], x["hold"])):
        logger.info("  K=%d H=%d -> exp=%+.4f (n=%d, drop=%s rise=%s)",
                    r["k"], r["hold"], r["expectancy"], r["n"],
                    r["expectancy_drop"], r["expectancy_rise"])

    # --- Regime split: per-direction expectancy in up vs down tape ---
    # No-exit baseline only (the sweep already shows exit geometry ~irrelevant
    # to expectancy). Answers: does gating a direction by SPY regime help, and
    # by how much, for rise vs drop separately. vol_h is unused when stop/target
    # are None, so 0.0 is safe here.
    regime_rows = []
    for k in KS:
        for hold in HOLDS:
            buckets: dict[tuple[str, str], list[float]] = {}
            for tr in selected[k]:
                path = paths.get(tr["ticker"])
                if path is None:
                    continue
                sim = _simulate(path, tr["date"], tr["direction"], 0.0, hold, None, None)
                if sim is None:
                    continue
                ret, _ = sim
                for reg in (tr["regime"], "all"):
                    buckets.setdefault((tr["direction"], reg), []).append(ret)
            for (direction, reg), rr in sorted(buckets.items()):
                arr = np.array(rr)
                regime_rows.append({
                    "k": k, "hold": hold, "direction": direction, "regime": reg,
                    "n": len(arr),
                    "expectancy": round(float(arr.mean()), 5),
                    "win_rate": round(float((arr > 0).mean()), 4),
                    "low_n": len(arr) < MIN_TRADES,
                })
    reg_path = _resolve_model_dir() / "regime_split_money_layer.json"
    reg_path.write_text(json.dumps({"rows": regime_rows}, indent=2))
    logger.info("Wrote %s", reg_path)
    logger.info("REGIME SPLIT (no-exit baseline, H=10):")
    for r in [x for x in regime_rows if x["hold"] == 10 and x["regime"] in ("up", "down", "all")]:
        logger.info("  K=%d %-4s %-5s -> exp=%+.4f wr=%.2f (n=%d)%s",
                    r["k"], r["direction"], r["regime"],
                    r["expectancy"], r["win_rate"], r["n"],
                    " [low-n]" if r["low_n"] else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
