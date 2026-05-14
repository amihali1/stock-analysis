"""Seed sweep for the rise-side excess-vs-SPY label experiment.

Reuses build_excess_dataset from train_directional_excess_experiment.py so we don't
rebuild the dataset N times. For each seed, retrains XGB + sigmoid calibrator with
same hyperparams + same walk-forward 3-fold + 70/15/15 split. Reports mean / std
/ min / max for fold AUCs and test AUC across N seeds. Output: /tmp/seed_sweep_rise_excess.json.
"""

from __future__ import annotations

import json
import logging
import statistics
import sys
from datetime import datetime

import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, roc_auc_score

sys.path.insert(0, "/tmp")
from excess_exp import build_excess_dataset  # noqa: E402

from src.models.directional import FEATURE_COLS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed-sweep")

SEEDS = list(range(10))  # 0..9
DIRECTION = "rise"
N_FOLDS = 3


def train_one_seed(df, seed: int) -> dict:
    dates = sorted(df["date"].unique())
    fold_size = len(dates) // (N_FOLDS + 1)

    fold_aucs = []
    for fold in range(N_FOLDS):
        train_end = (fold + 1) * fold_size
        val_end = train_end + fold_size
        train_dates = dates[:train_end]
        val_dates = dates[train_end:val_end]

        train_mask = df["date"].isin(train_dates)
        val_mask = df["date"].isin(val_dates)
        X_tr, y_tr = df.loc[train_mask, FEATURE_COLS], df.loc[train_mask, "label"]
        X_va, y_va = df.loc[val_mask, FEATURE_COLS], df.loc[val_mask, "label"]
        if len(X_va) == 0 or len(set(y_va)) < 2:
            continue

        model = xgb.XGBClassifier(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            scale_pos_weight=len(y_tr[y_tr == 0]) / max(len(y_tr[y_tr == 1]), 1),
            eval_metric="logloss", random_state=seed, n_jobs=-1,
        )
        model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
        proba = model.predict_proba(X_va)[:, 1]
        fold_aucs.append(float(roc_auc_score(y_va, proba)))

    n_dates = len(dates)
    train_end = int(n_dates * 0.70)
    calib_end = int(n_dates * 0.85)
    train_set = set(dates[:train_end])
    calib_set = set(dates[train_end:calib_end])
    test_set = set(dates[calib_end:])

    X_tr = df.loc[df["date"].isin(train_set), FEATURE_COLS]
    y_tr = df.loc[df["date"].isin(train_set), "label"]
    X_ca = df.loc[df["date"].isin(calib_set), FEATURE_COLS]
    y_ca = df.loc[df["date"].isin(calib_set), "label"]
    X_te = df.loc[df["date"].isin(test_set), FEATURE_COLS]
    y_te = df.loc[df["date"].isin(test_set), "label"]

    final = xgb.XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=len(y_tr[y_tr == 0]) / max(len(y_tr[y_tr == 1]), 1),
        eval_metric="logloss", random_state=seed, n_jobs=-1,
    )
    final.fit(X_tr, y_tr, verbose=False)

    calibrator = None
    if len(X_ca) >= 200 and len(set(y_ca)) > 1:
        calibrator = CalibratedClassifierCV(estimator=final, cv="prefit", method="sigmoid")
        calibrator.fit(X_ca, y_ca)

    proba_te = (calibrator if calibrator is not None else final).predict_proba(X_te)[:, 1]
    test_auc = float(roc_auc_score(y_te, proba_te)) if len(set(y_te)) > 1 else 0.0
    brier = float(brier_score_loss(y_te, proba_te)) if len(set(y_te)) > 1 else None

    return {
        "seed": seed,
        "fold_aucs": fold_aucs,
        "test_auc": test_auc,
        "test_brier": brier,
    }


def agg(values: list[float]) -> dict:
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "std": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
    }


def main() -> int:
    logger.info("Building dataset once (direction=%s)...", DIRECTION)
    df = build_excess_dataset(DIRECTION)
    logger.info("Rows=%d pos_rate=%.4f", len(df), float(df["label"].mean()))

    runs = []
    for s in SEEDS:
        r = train_one_seed(df, s)
        logger.info(
            "seed=%d folds=%s test_auc=%.4f brier=%.4f",
            s,
            [round(a, 4) for a in r["fold_aucs"]],
            r["test_auc"],
            r["test_brier"] if r["test_brier"] is not None else float("nan"),
        )
        runs.append(r)

    test_aucs = [r["test_auc"] for r in runs]
    briers = [r["test_brier"] for r in runs if r["test_brier"] is not None]
    fold_by_idx = {i: [r["fold_aucs"][i] for r in runs if len(r["fold_aucs"]) > i] for i in range(N_FOLDS)}

    summary = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "direction": DIRECTION,
        "n_seeds": len(SEEDS),
        "test_auc": agg(test_aucs),
        "test_brier": agg(briers),
        "fold_aucs": {f"fold_{i+1}": agg(v) for i, v in fold_by_idx.items() if v},
        "runs": runs,
    }
    out = "/tmp/seed_sweep_rise_excess.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    logger.info("Wrote %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
