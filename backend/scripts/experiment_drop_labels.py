"""Drop-side label reformulation sweep (post-P10-009 follow-up).

Three consecutive additive-feature retrains (SI / insider / 8-K) failed
the drop AUC gate. Root cause flagged 2026-05-14: macro-day domination
overwhelms per-ticker signal. This script tests four label formulations
to see if any of them recovers per-ticker discrimination:

    1. absolute (baseline)         — fwd_return < -3%
    2. excess_vs_spy               — (fwd_return - spy_fwd) < -3%
    3. vol_normalized              — fwd_return < -k * realized_vol_20d
    4. cross_sectional_decile      — per-date bottom decile of fwd_return

5-seed sweep per mode (different XGB random_state), trained on identical
feature matrix and time split. Reports mean ± std test AUC + brier. No
model promotion — research-only.

Run inside container:

    docker exec backend-backend-1 python -m scripts.experiment_drop_labels
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, roc_auc_score

from src.db.session import SessionLocal
from src.db.models import PriceHistory
from src.models.directional import (
    FEATURE_COLS,
    FORWARD_DAYS,
    SPY_TICKER,
    build_dataset,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("experiment_drop_labels")

DROP_THRESHOLD = -0.03  # absolute & excess modes
VOL_K = 1.5             # vol_normalized: fwd_return < -K * vol_20d
DECILE = 0.10           # cross_sectional: bottom 10% per date


def _load_forward_returns(tickers: list[str] | None) -> pd.DataFrame:
    """Pull (ticker, date, close, fwd_close) for forward-return derivation.

    Returns DataFrame with `fwd_return` column. SPY forward return is
    merged in for the excess-vs-SPY mode.
    """
    db = SessionLocal()
    try:
        q = db.query(PriceHistory.ticker, PriceHistory.date, PriceHistory.close)
        if tickers:
            q = q.filter(PriceHistory.ticker.in_(tickers + [SPY_TICKER]))
        rows = q.all()
    finally:
        db.close()
    df = pd.DataFrame(rows, columns=["ticker", "date", "close"])
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    df["fwd_close"] = df.groupby("ticker")["close"].shift(-FORWARD_DAYS)
    df["fwd_return"] = df["fwd_close"] / df["close"] - 1

    # 20-day realized volatility (annualized) for vol_normalized threshold
    df["daily_ret"] = df.groupby("ticker")["close"].pct_change()
    df["vol_20d"] = (
        df.groupby("ticker")["daily_ret"]
        .rolling(20)
        .std()
        .reset_index(level=0, drop=True)
        * np.sqrt(252)
    )
    return df[["ticker", "date", "fwd_return", "vol_20d"]]


def _attach_spy_fwd(df: pd.DataFrame) -> pd.DataFrame:
    spy = df.loc[df["ticker"] == SPY_TICKER, ["date", "fwd_return"]].rename(
        columns={"fwd_return": "spy_fwd"}
    )
    return df.merge(spy, on="date", how="left")


def _label_absolute(df: pd.DataFrame) -> pd.Series:
    return (df["fwd_return"] < DROP_THRESHOLD).astype(int)


def _label_excess(df: pd.DataFrame) -> pd.Series:
    excess = df["fwd_return"] - df["spy_fwd"]
    return (excess < DROP_THRESHOLD).astype(int)


def _label_vol_normalized(df: pd.DataFrame) -> pd.Series:
    # fwd_return is a 5-day return; vol_20d is annualized. Scale vol to
    # the 5-day horizon: vol_5d = vol_20d / sqrt(252/5) ≈ vol_20d / 7.1
    vol_5d = df["vol_20d"] / np.sqrt(252.0 / FORWARD_DAYS)
    return (df["fwd_return"] < -VOL_K * vol_5d).astype(int)


def _label_cross_sectional(df: pd.DataFrame) -> pd.Series:
    """Per-date bottom decile of fwd_return."""
    # rank within (date) — pct rank 0..1; label=1 if rank <= DECILE
    df = df.copy()
    df["fwd_rank"] = df.groupby("date")["fwd_return"].rank(pct=True)
    return (df["fwd_rank"] <= DECILE).astype(int)


LABEL_FNS = {
    "absolute": _label_absolute,
    "excess_vs_spy": _label_excess,
    "vol_normalized": _label_vol_normalized,
    "cross_sectional_decile": _label_cross_sectional,
}


def _train_one(
    X_train, y_train, X_calib, y_calib, X_test, y_test, seed: int
) -> tuple[float, float]:
    """Train XGB → sigmoid calibrate → return (test_auc, test_brier)."""
    scale_pos_weight = len(y_train[y_train == 0]) / max(len(y_train[y_train == 1]), 1)
    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos_weight,
        eval_metric="logloss",
        random_state=seed,
        n_jobs=-1,
    )
    model.fit(X_train, y_train, verbose=False)

    calibrator = None
    if len(X_calib) >= 200 and len(set(y_calib)) > 1:
        try:
            calibrator = CalibratedClassifierCV(
                estimator=model, cv="prefit", method="sigmoid"
            )
            calibrator.fit(X_calib, y_calib)
        except Exception:
            calibrator = None

    estimator = calibrator if calibrator is not None else model
    probs = estimator.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, probs) if len(set(y_test)) > 1 else float("nan")
    brier = brier_score_loss(y_test, probs) if len(set(y_test)) > 1 else float("nan")
    return float(auc), float(brier)


def _sweep_mode(df: pd.DataFrame, mode: str, seeds: list[int]) -> dict:
    label_fn = LABEL_FNS[mode]
    df = df.copy()
    df["label"] = label_fn(df)
    # Drop rows where label couldn't be computed (NaN inputs).
    df = df.dropna(subset=["label"])
    df["label"] = df["label"].astype(int)

    dates = sorted(df["date"].unique())
    n = len(dates)
    train_end = int(n * 0.70)
    calib_end = int(n * 0.85)
    train_set = set(dates[:train_end])
    calib_set = set(dates[train_end:calib_end])
    test_set = set(dates[calib_end:])

    X_train = df.loc[df["date"].isin(train_set), FEATURE_COLS]
    y_train = df.loc[df["date"].isin(train_set), "label"]
    X_calib = df.loc[df["date"].isin(calib_set), FEATURE_COLS]
    y_calib = df.loc[df["date"].isin(calib_set), "label"]
    X_test = df.loc[df["date"].isin(test_set), FEATURE_COLS]
    y_test = df.loc[df["date"].isin(test_set), "label"]

    pos_rate = float(df["label"].mean())
    logger.info(
        "[%s] rows=%d positive_rate=%.3f train=%d calib=%d test=%d",
        mode, len(df), pos_rate, len(X_train), len(X_calib), len(X_test),
    )

    aucs, briers = [], []
    for seed in seeds:
        auc, brier = _train_one(X_train, y_train, X_calib, y_calib, X_test, y_test, seed)
        aucs.append(auc)
        briers.append(brier)
        logger.info("  seed=%d auc=%.4f brier=%.4f", seed, auc, brier)

    return {
        "mode": mode,
        "n_rows": len(df),
        "positive_rate": pos_rate,
        "n_train": len(X_train),
        "n_calib": len(X_calib),
        "n_test": len(X_test),
        "seeds": seeds,
        "aucs": aucs,
        "briers": briers,
        "auc_mean": statistics.mean(aucs),
        "auc_std": statistics.stdev(aucs) if len(aucs) > 1 else 0.0,
        "brier_mean": statistics.mean(briers),
        "brier_std": statistics.stdev(briers) if len(briers) > 1 else 0.0,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Drop label reformulation sweep")
    p.add_argument("--seeds", type=str, default="42,7,13,29,101",
                   help="Comma-separated XGB random_state values")
    p.add_argument("--modes", type=str, default=",".join(LABEL_FNS.keys()),
                   help="Comma-separated label modes to test")
    p.add_argument("--output", type=str,
                   default="trained_models/experiment_drop_labels.json")
    p.add_argument("--tickers", type=str, default=None,
                   help="Comma-separated ticker subset (default = full watchlist)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    seeds = [int(s.strip()) for s in args.seeds.split(",")]
    modes = [m.strip() for m in args.modes.split(",")]
    tickers = (
        [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else None
    )

    # Build feature matrix once with absolute label (we override labels per mode).
    logger.info("Building feature dataset (using FEATURE_COLS, n=%d)...", len(FEATURE_COLS))
    base = build_dataset(tickers=tickers, direction="drop", label_mode="absolute")
    logger.info("Base dataset: %d rows", len(base))

    # Pull raw forward returns + vol so we can recompute labels per mode.
    logger.info("Loading forward returns + SPY fwd + vol_20d...")
    fwd = _load_forward_returns(tickers)
    fwd = _attach_spy_fwd(fwd)
    base = base.drop(columns=["label"]).merge(
        fwd, on=["ticker", "date"], how="left"
    )
    base = base.dropna(subset=["fwd_return", "vol_20d", "spy_fwd"])
    logger.info("After fwd-return merge: %d rows", len(base))

    results = []
    for mode in modes:
        if mode not in LABEL_FNS:
            logger.warning("Unknown mode %r, skipping", mode)
            continue
        logger.info("=== Mode: %s ===", mode)
        results.append(_sweep_mode(base, mode, seeds))

    out = {
        "feature_cols": FEATURE_COLS,
        "n_features": len(FEATURE_COLS),
        "forward_days": FORWARD_DAYS,
        "drop_threshold": DROP_THRESHOLD,
        "vol_k": VOL_K,
        "decile": DECILE,
        "seeds": seeds,
        "results": results,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(out, f, indent=2)
    logger.info("Wrote results to %s", output_path)

    print()
    print(f"{'mode':<26} {'pos_rate':>9} {'auc_mean':>10} {'auc_std':>9} {'brier_mean':>11} {'brier_std':>10}")
    print("-" * 80)
    for r in results:
        print(
            f"{r['mode']:<26} "
            f"{r['positive_rate']:>9.3f} "
            f"{r['auc_mean']:>10.4f} "
            f"{r['auc_std']:>9.4f} "
            f"{r['brier_mean']:>11.4f} "
            f"{r['brier_std']:>10.4f}"
        )

    baseline = next((r for r in results if r["mode"] == "absolute"), None)
    if baseline is not None:
        print()
        print(f"Deltas vs 'absolute' baseline (auc_mean={baseline['auc_mean']:.4f}):")
        for r in results:
            if r["mode"] == "absolute":
                continue
            print(f"  {r['mode']:<26} ΔAUC={r['auc_mean']-baseline['auc_mean']:+.4f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
