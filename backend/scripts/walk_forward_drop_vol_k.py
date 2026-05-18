"""Walk-forward validation of vol-normalized drop label at K=1.75 and K=2.5.

Final-slice test in `sweep_drop_vol_k.py` showed AUC 0.642 (K=1.75) and
0.666 (K=2.5) but used only the last 15% of dates as test. Walk-forward
exposes time-period overfit: train on first chunk, test on next chunk,
slide forward. AUC must hold across all folds, not just the most recent
one, before we trust the label.

3 folds, 5 seeds per fold per K. Each fold trains on a
backward-truncated window and tests on the following chunk:

    fold 1: train on dates[0:N/4],     test on dates[N/4:2N/4]
    fold 2: train on dates[0:2N/4],    test on dates[2N/4:3N/4]
    fold 3: train on dates[0:3N/4],    test on dates[3N/4:]

Run inside container:

    docker exec backend-backend-1 python -m scripts.walk_forward_drop_vol_k
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
logger = logging.getLogger("walk_forward_drop_vol_k")


def _load_vol(tickers):
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


def _train_one(X_train, y_train, X_test, y_test, seed):
    """No separate calibration set for walk-forward — AUC is rank-only
    so calibration is irrelevant. Skipping calibration speeds the sweep."""
    if len(set(y_train)) < 2 or len(set(y_test)) < 2:
        return float("nan"), float("nan")
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
    probs = model.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, probs)
    brier = brier_score_loss(y_test, probs)
    return float(auc), float(brier)


def _walk_forward(df: pd.DataFrame, k: float, n_folds: int, seeds: list[int]) -> dict:
    d = df.copy()
    d["label"] = (d["fwd_return"] < -k * d["vol_5d"]).astype(int)
    d = d.dropna(subset=["label", "fwd_return", "vol_5d"])
    d["label"] = d["label"].astype(int)

    dates = sorted(d["date"].unique())
    n = len(dates)
    fold_size = n // (n_folds + 1)

    fold_results = []
    for fold in range(n_folds):
        train_end = (fold + 1) * fold_size
        test_end = train_end + fold_size if fold < n_folds - 1 else n
        train_dates = set(dates[:train_end])
        test_dates = set(dates[train_end:test_end])

        X_train = d.loc[d["date"].isin(train_dates), FEATURE_COLS]
        y_train = d.loc[d["date"].isin(train_dates), "label"]
        X_test = d.loc[d["date"].isin(test_dates), FEATURE_COLS]
        y_test = d.loc[d["date"].isin(test_dates), "label"]

        pos_train = float(y_train.mean()) if len(y_train) else 0.0
        pos_test = float(y_test.mean()) if len(y_test) else 0.0

        aucs, briers = [], []
        for seed in seeds:
            auc, brier = _train_one(X_train, y_train, X_test, y_test, seed)
            aucs.append(auc)
            briers.append(brier)

        valid_aucs = [a for a in aucs if not (a != a)]  # filter NaN
        valid_briers = [b for b in briers if not (b != b)]

        fr = {
            "fold": fold + 1,
            "train_size": int(len(X_train)),
            "test_size": int(len(X_test)),
            "pos_rate_train": pos_train,
            "pos_rate_test": pos_test,
            "aucs": aucs,
            "auc_mean": statistics.mean(valid_aucs) if valid_aucs else float("nan"),
            "auc_std": statistics.stdev(valid_aucs) if len(valid_aucs) > 1 else 0.0,
            "brier_mean": statistics.mean(valid_briers) if valid_briers else float("nan"),
        }
        fold_results.append(fr)
        logger.info(
            "[K=%.2f fold=%d] train=%d test=%d pos_train=%.3f pos_test=%.3f auc=%.4f ± %.4f",
            k, fold + 1, fr["train_size"], fr["test_size"],
            pos_train, pos_test, fr["auc_mean"], fr["auc_std"],
        )

    all_aucs = [a for fr in fold_results for a in fr["aucs"] if not (a != a)]
    overall_mean = statistics.mean(all_aucs) if all_aucs else float("nan")
    overall_std = statistics.stdev(all_aucs) if len(all_aucs) > 1 else 0.0
    return {
        "k": k,
        "n_folds": n_folds,
        "seeds": seeds,
        "folds": fold_results,
        "overall_auc_mean": overall_mean,
        "overall_auc_std": overall_std,
    }


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Walk-forward validation for vol-normalized drop label")
    p.add_argument("--ks", type=str, default="1.75,2.50")
    p.add_argument("--seeds", type=str, default="42,7,13,29,101")
    p.add_argument("--n-folds", type=int, default=3)
    p.add_argument("--output", type=str,
                   default="trained_models/walk_forward_drop_vol_k.json")
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
    vol = _load_vol(tickers)
    base = base.merge(vol, on=["ticker", "date"], how="left")
    base = base.dropna(subset=["fwd_return", "vol_5d"])
    logger.info("Base dataset: %d rows", len(base))

    results = []
    for k in ks:
        logger.info("=== K = %.2f ===", k)
        results.append(_walk_forward(base, k, args.n_folds, seeds))

    out = {
        "feature_cols": FEATURE_COLS,
        "n_features": len(FEATURE_COLS),
        "forward_days": FORWARD_DAYS,
        "ks": ks,
        "seeds": seeds,
        "n_folds": args.n_folds,
        "results": results,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(out, f, indent=2)
    logger.info("Wrote results to %s", output_path)

    print()
    for r in results:
        print(f"=== K = {r['k']:.2f} ===")
        print(f"{'fold':>5} {'train_n':>8} {'test_n':>8} {'pos_train':>10} {'pos_test':>10} {'auc_mean':>10} {'auc_std':>9}")
        for fr in r["folds"]:
            print(
                f"{fr['fold']:>5d} "
                f"{fr['train_size']:>8d} "
                f"{fr['test_size']:>8d} "
                f"{fr['pos_rate_train']:>10.4f} "
                f"{fr['pos_rate_test']:>10.4f} "
                f"{fr['auc_mean']:>10.4f} "
                f"{fr['auc_std']:>9.4f}"
            )
        print(f"  Overall: AUC {r['overall_auc_mean']:.4f} ± {r['overall_auc_std']:.4f}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
