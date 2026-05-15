"""10-seed sweep on rise v3 (excess label + z-features, 67-feature set).

Determines whether rise v3's single-seed test AUC (0.6175) is within rise
v2's measured seed range (0.616-0.631, mean 0.6257 ± 0.0047) or a genuine
drift down caused by the new features.

Reuses build_dataset to keep label/feature semantics identical to
DirectionalModel.train. Re-fits a fresh XGB per seed using the same
walk-forward 3-fold layout and sigmoid calibration on the disjoint 15%
holdout, mirroring production training.

Usage:
    python scripts/seed_sweep_rise_v3.py --seeds 10
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from statistics import mean, pstdev

import numpy as np
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, roc_auc_score

from src.models.directional import (
    FEATURE_COLS,
    LABEL_MODE_EXCESS,
    PER_TICKER_RANK_FEATURE_COLS,
    build_dataset,
)

# Rise v3 = production base + per-ticker z-feats. After 2026-05-15 the z-feats
# were removed from the production FEATURE_COLS list (negative result on the
# recent test slice), so this script now opts in explicitly.
RISE_V3_FEATURE_COLS = FEATURE_COLS + PER_TICKER_RANK_FEATURE_COLS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed_sweep_rise_v3")


def _train_one(df, seed: int, n_folds: int = 3) -> dict:
    feature_cols = RISE_V3_FEATURE_COLS
    dates = sorted(df["date"].unique())
    fold_size = len(dates) // (n_folds + 2)  # leave room for calib + test

    fold_aucs: list[float] = []
    for fold in range(n_folds):
        train_end_idx = (fold + 1) * fold_size
        val_end_idx = train_end_idx + fold_size
        train_dates = dates[:train_end_idx]
        val_dates = dates[train_end_idx:val_end_idx]
        train_mask = df["date"].isin(train_dates)
        val_mask = df["date"].isin(val_dates)
        X_tr = df.loc[train_mask, feature_cols]
        y_tr = df.loc[train_mask, "label"]
        X_va = df.loc[val_mask, feature_cols]
        y_va = df.loc[val_mask, "label"]
        if len(X_tr) == 0 or len(X_va) == 0:
            continue
        pos_w = len(y_tr[y_tr == 0]) / max(len(y_tr[y_tr == 1]), 1)
        m = xgb.XGBClassifier(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            scale_pos_weight=pos_w, eval_metric="logloss",
            random_state=seed, n_jobs=-1,
        )
        m.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
        if y_va.nunique() > 1:
            fold_aucs.append(float(roc_auc_score(y_va, m.predict_proba(X_va)[:, 1])))

    # Final time-ordered three-way split for train / calib / test
    n = len(dates)
    train_end = int(n * 0.70)
    calib_end = int(n * 0.85)
    train_dates = dates[:train_end]
    calib_dates = dates[train_end:calib_end]
    test_dates = dates[calib_end:]
    train_mask = df["date"].isin(train_dates)
    calib_mask = df["date"].isin(calib_dates)
    test_mask = df["date"].isin(test_dates)
    X_tr = df.loc[train_mask, feature_cols]
    y_tr = df.loc[train_mask, "label"]
    X_ca = df.loc[calib_mask, feature_cols]
    y_ca = df.loc[calib_mask, "label"]
    X_te = df.loc[test_mask, feature_cols]
    y_te = df.loc[test_mask, "label"]

    pos_w = len(y_tr[y_tr == 0]) / max(len(y_tr[y_tr == 1]), 1)
    base = xgb.XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=pos_w, eval_metric="logloss",
        random_state=seed, n_jobs=-1,
    )
    base.fit(X_tr, y_tr, verbose=False)
    calib = CalibratedClassifierCV(base, cv="prefit", method="sigmoid")
    calib.fit(X_ca, y_ca)
    probs = calib.predict_proba(X_te)[:, 1]
    test_auc = float(roc_auc_score(y_te, probs)) if y_te.nunique() > 1 else float("nan")
    test_brier = float(brier_score_loss(y_te, probs))

    return {
        "seed": seed,
        "fold_aucs": fold_aucs,
        "test_auc": test_auc,
        "test_brier": test_brier,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--out", type=str,
                        default="/app/trained_models/seed_sweep_rise_v3.json")
    args = parser.parse_args()

    logger.info("Building rise dataset (label_mode=excess, %d features)...",
                len(RISE_V3_FEATURE_COLS))
    df = build_dataset(direction="rise", label_mode=LABEL_MODE_EXCESS,
                       feature_cols=RISE_V3_FEATURE_COLS)
    logger.info("Dataset: %d rows, %d positive (%.1f%%)",
                len(df), int(df["label"].sum()), df["label"].mean() * 100)

    seeds = list(range(args.seeds))
    results = []
    for s in seeds:
        logger.info("=== seed=%d ===", s)
        r = _train_one(df, seed=s)
        results.append(r)
        logger.info("seed=%d test_auc=%.4f brier=%.4f folds=%s",
                    s, r["test_auc"], r["test_brier"],
                    [f"{x:.3f}" for x in r["fold_aucs"]])

    test_aucs = [r["test_auc"] for r in results if not np.isnan(r["test_auc"])]
    payload = {
        "n_seeds": len(seeds),
        "n_features": len(RISE_V3_FEATURE_COLS),
        "direction": "rise",
        "label_mode": "excess",
        "test_auc_mean": float(mean(test_aucs)),
        "test_auc_std": float(pstdev(test_aucs)) if len(test_aucs) > 1 else 0.0,
        "test_auc_min": float(min(test_aucs)),
        "test_auc_max": float(max(test_aucs)),
        "results": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Wrote %s", out)

    print(f"\nSeed sweep ({len(test_aucs)} seeds, {len(RISE_V3_FEATURE_COLS)} features, rise excess):")
    print(f"  test AUC mean : {payload['test_auc_mean']:.4f}")
    print(f"  test AUC std  : {payload['test_auc_std']:.4f}")
    print(f"  test AUC range: [{payload['test_auc_min']:.4f}, {payload['test_auc_max']:.4f}]")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
