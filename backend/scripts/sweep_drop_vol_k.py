"""Vol-normalized drop label: sweep K threshold.

Follow-up to `experiment_drop_labels.py`. Vol-normalized label
(fwd_return < -K * vol_5d) lifted drop AUC by +0.073 at K=1.5 — but
K=1.5 was an unjustified guess. Sweep K to find the AUC peak and check
the base-rate / AUC trade curve.

Lower K → more positives (label fires more often) → noisier signal
Higher K → fewer positives, sharper "real catalyst" signal but smaller
training population and risk of overfit

5-seed sweep per K; identical 63-feature matrix; identical time split.

Run inside container:

    docker exec backend-backend-1 python -m scripts.sweep_drop_vol_k
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
    build_dataset,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sweep_drop_vol_k")


def _load_vol(tickers: list[str] | None) -> pd.DataFrame:
    db = SessionLocal()
    try:
        q = db.query(PriceHistory.ticker, PriceHistory.date, PriceHistory.close)
        if tickers:
            q = q.filter(PriceHistory.ticker.in_(tickers))
        rows = q.all()
    finally:
        db.close()
    df = pd.DataFrame(rows, columns=["ticker", "date", "close"])
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    df["fwd_close"] = df.groupby("ticker")["close"].shift(-FORWARD_DAYS)
    df["fwd_return"] = df["fwd_close"] / df["close"] - 1
    df["daily_ret"] = df.groupby("ticker")["close"].pct_change()
    df["vol_20d"] = (
        df.groupby("ticker")["daily_ret"]
        .rolling(20)
        .std()
        .reset_index(level=0, drop=True)
        * np.sqrt(252)
    )
    df["vol_5d"] = df["vol_20d"] / np.sqrt(252.0 / FORWARD_DAYS)
    return df[["ticker", "date", "fwd_return", "vol_5d"]]


def _train_one(X_train, y_train, X_calib, y_calib, X_test, y_test, seed):
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


def _sweep_k(df: pd.DataFrame, k: float, seeds: list[int]) -> dict:
    d = df.copy()
    d["label"] = (d["fwd_return"] < -k * d["vol_5d"]).astype(int)
    d = d.dropna(subset=["label", "fwd_return", "vol_5d"])
    d["label"] = d["label"].astype(int)

    dates = sorted(d["date"].unique())
    n = len(dates)
    train_end = int(n * 0.70)
    calib_end = int(n * 0.85)
    train_set = set(dates[:train_end])
    calib_set = set(dates[train_end:calib_end])
    test_set = set(dates[calib_end:])

    X_train = d.loc[d["date"].isin(train_set), FEATURE_COLS]
    y_train = d.loc[d["date"].isin(train_set), "label"]
    X_calib = d.loc[d["date"].isin(calib_set), FEATURE_COLS]
    y_calib = d.loc[d["date"].isin(calib_set), "label"]
    X_test = d.loc[d["date"].isin(test_set), FEATURE_COLS]
    y_test = d.loc[d["date"].isin(test_set), "label"]

    pos_rate = float(d["label"].mean())
    logger.info(
        "[K=%.2f] rows=%d positive_rate=%.3f train=%d calib=%d test=%d",
        k, len(d), pos_rate, len(X_train), len(X_calib), len(X_test),
    )

    aucs, briers = [], []
    for seed in seeds:
        auc, brier = _train_one(X_train, y_train, X_calib, y_calib, X_test, y_test, seed)
        aucs.append(auc)
        briers.append(brier)
        logger.info("  seed=%d auc=%.4f brier=%.4f", seed, auc, brier)

    return {
        "k": k,
        "n_rows": len(d),
        "positive_rate": pos_rate,
        "seeds": seeds,
        "aucs": aucs,
        "briers": briers,
        "auc_mean": statistics.mean(aucs),
        "auc_std": statistics.stdev(aucs) if len(aucs) > 1 else 0.0,
        "brier_mean": statistics.mean(briers),
        "brier_std": statistics.stdev(briers) if len(briers) > 1 else 0.0,
    }


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="K-threshold sweep for vol-normalized drop label")
    p.add_argument("--ks", type=str, default="1.0,1.25,1.5,1.75,2.0,2.5",
                   help="Comma-separated K values to test")
    p.add_argument("--seeds", type=str, default="42,7,13,29,101")
    p.add_argument("--output", type=str,
                   default="trained_models/sweep_drop_vol_k.json")
    p.add_argument("--tickers", type=str, default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    ks = [float(x.strip()) for x in args.ks.split(",")]
    seeds = [int(s.strip()) for s in args.seeds.split(",")]
    tickers = (
        [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else None
    )

    logger.info("Building feature dataset (FEATURE_COLS n=%d)...", len(FEATURE_COLS))
    base = build_dataset(tickers=tickers, direction="drop", label_mode="absolute")
    base = base.drop(columns=["label"])
    logger.info("Base dataset: %d rows", len(base))

    logger.info("Loading forward returns + vol_5d...")
    vol = _load_vol(tickers)
    base = base.merge(vol, on=["ticker", "date"], how="left")
    base = base.dropna(subset=["fwd_return", "vol_5d"])
    logger.info("After merge: %d rows", len(base))

    results = []
    for k in ks:
        logger.info("=== K = %.2f ===", k)
        results.append(_sweep_k(base, k, seeds))

    out = {
        "feature_cols": FEATURE_COLS,
        "n_features": len(FEATURE_COLS),
        "forward_days": FORWARD_DAYS,
        "ks": ks,
        "seeds": seeds,
        "results": results,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(out, f, indent=2)
    logger.info("Wrote results to %s", output_path)

    print()
    print(f"{'K':>5} {'pos_rate':>10} {'auc_mean':>10} {'auc_std':>9} {'brier_mean':>11}")
    print("-" * 55)
    for r in results:
        print(
            f"{r['k']:>5.2f} "
            f"{r['positive_rate']:>10.4f} "
            f"{r['auc_mean']:>10.4f} "
            f"{r['auc_std']:>9.4f} "
            f"{r['brier_mean']:>11.4f}"
        )

    best = max(results, key=lambda r: r["auc_mean"])
    print()
    print(f"Best K = {best['k']:.2f}  AUC mean = {best['auc_mean']:.4f} ± {best['auc_std']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
