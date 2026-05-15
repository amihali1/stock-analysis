"""Z-window sweep on rise side (excess label).

The 2026-05-15 single-window (z120) seed sweep showed a ~1.2pp AUC regression
vs rise v2 on the recent test slice (see zfeats_retrain_negative_2026-05-15
memo). Hypothesis: 120 trading days is too long for the regime-shifted
recent slice, so the z-scores under-react to the current regime. Test
shorter windows (z60, z90) plus the original z120 control.

Uses production FEATURE_COLS as the base (no z-feats in directional.py post
2026-05-15 cleanup); z-cols are computed in-script per window from the
source columns retained in FEATURE_COLS (return_5d_lag, macd, etc).

Per window: N seeds of walk-forward (3 cv folds + final 70/15/15
train/calib/test) with sigmoid prefit calibration on the disjoint holdout,
mirroring DirectionalModel.train.

Usage:
    python scripts/sweep_z_window.py --seeds 5 --windows 60 90 120
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from statistics import mean, pstdev

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, roc_auc_score

from src.models.directional import (
    FEATURE_COLS,
    LABEL_MODE_ABSOLUTE,
    LABEL_MODE_EXCESS,
    PER_TICKER_RANK_SOURCE_COLS,
    build_dataset,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sweep_z_window")


def _add_z_features(df: pd.DataFrame, window: int, min_periods: int = 30) -> tuple[pd.DataFrame, list[str]]:
    """Compute per-ticker rolling z-scores for the given window.

    Drops rows where any new z-column is NaN (head of each ticker series).
    """
    out = df.sort_values(["ticker", "date"]).copy()
    new_cols: list[str] = []
    for src in PER_TICKER_RANK_SOURCE_COLS:
        if src not in out.columns:
            raise KeyError(f"Source column {src!r} missing from dataframe")
        z_col = f"{src}_z{window}"
        rolling_mean = out.groupby("ticker")[src].transform(
            lambda s: s.rolling(window, min_periods=min_periods).mean()
        )
        rolling_std = out.groupby("ticker")[src].transform(
            lambda s: s.rolling(window, min_periods=min_periods).std()
        )
        std_safe = rolling_std.replace(0, np.nan)
        out[z_col] = (out[src] - rolling_mean) / std_safe
        new_cols.append(z_col)
    return out.dropna(subset=new_cols).reset_index(drop=True), new_cols


def _train_one(df: pd.DataFrame, feature_cols: list[str], seed: int, n_folds: int = 3) -> dict:
    dates = sorted(df["date"].unique())
    fold_size = len(dates) // (n_folds + 2)

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
    if y_ca.nunique() < 2:
        logger.warning("seed=%d calib slice single-class; skipping calibrator", seed)
        probs = base.predict_proba(X_te)[:, 1]
    else:
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
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--windows", nargs="+", type=int, default=[60, 90, 120])
    parser.add_argument("--direction", choices=["rise", "drop"], default="rise")
    parser.add_argument("--label-mode", choices=["absolute", "excess"],
                        default=None,
                        help="Default: excess for rise, absolute for drop")
    parser.add_argument("--out", type=str,
                        default="/app/trained_models/sweep_z_window.json")
    args = parser.parse_args()

    if args.label_mode is None:
        label_mode = LABEL_MODE_EXCESS if args.direction == "rise" else LABEL_MODE_ABSOLUTE
    else:
        label_mode = args.label_mode

    logger.info("Building %s dataset (label_mode=%s, base FEATURE_COLS n=%d)...",
                args.direction, label_mode, len(FEATURE_COLS))
    base_df = build_dataset(direction=args.direction, label_mode=label_mode)
    logger.info("Base dataset: %d rows, %d positive (%.1f%%)",
                len(base_df), int(base_df["label"].sum()), base_df["label"].mean() * 100)

    summary = []
    full_results = {}

    for window in args.windows:
        logger.info("=== window=%d ===", window)
        df_w, z_cols = _add_z_features(base_df, window=window)
        feature_cols = list(FEATURE_COLS) + z_cols
        logger.info("window=%d post-zdrop rows=%d (lost %d to head NaN), features=%d",
                    window, len(df_w), len(base_df) - len(df_w), len(feature_cols))

        per_seed = []
        for s in range(args.seeds):
            r = _train_one(df_w, feature_cols=feature_cols, seed=s)
            per_seed.append(r)
            logger.info("  seed=%d test_auc=%.4f brier=%.4f folds=%s",
                        s, r["test_auc"], r["test_brier"],
                        [f"{x:.3f}" for x in r["fold_aucs"]])

        test_aucs = [r["test_auc"] for r in per_seed if not np.isnan(r["test_auc"])]
        stats = {
            "window": window,
            "n_seeds": len(per_seed),
            "n_features": len(feature_cols),
            "n_rows": len(df_w),
            "test_auc_mean": float(mean(test_aucs)) if test_aucs else float("nan"),
            "test_auc_std": float(pstdev(test_aucs)) if len(test_aucs) > 1 else 0.0,
            "test_auc_min": float(min(test_aucs)) if test_aucs else float("nan"),
            "test_auc_max": float(max(test_aucs)) if test_aucs else float("nan"),
        }
        summary.append(stats)
        full_results[str(window)] = per_seed

    # Baseline reference (no z-feats): one fast pass for comparison
    logger.info("=== baseline (no z-feats) ===")
    baseline_seeds = []
    for s in range(args.seeds):
        r = _train_one(base_df, feature_cols=list(FEATURE_COLS), seed=s)
        baseline_seeds.append(r)
        logger.info("  seed=%d test_auc=%.4f brier=%.4f",
                    s, r["test_auc"], r["test_brier"])
    baseline_aucs = [r["test_auc"] for r in baseline_seeds if not np.isnan(r["test_auc"])]
    baseline_stats = {
        "window": None,
        "n_seeds": len(baseline_seeds),
        "n_features": len(FEATURE_COLS),
        "n_rows": len(base_df),
        "test_auc_mean": float(mean(baseline_aucs)) if baseline_aucs else float("nan"),
        "test_auc_std": float(pstdev(baseline_aucs)) if len(baseline_aucs) > 1 else 0.0,
        "test_auc_min": float(min(baseline_aucs)) if baseline_aucs else float("nan"),
        "test_auc_max": float(max(baseline_aucs)) if baseline_aucs else float("nan"),
    }
    summary.insert(0, baseline_stats)
    full_results["baseline"] = baseline_seeds

    payload = {
        "seeds": args.seeds,
        "direction": args.direction,
        "label_mode": label_mode,
        "summary": summary,
        "results": full_results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Wrote %s", out)

    print("\nWindow | n_feats | rows  | mean AUC | std    | min    | max")
    print("-------|---------|-------|----------|--------|--------|-------")
    for s in summary:
        label = "base" if s["window"] is None else f"z{s['window']}"
        print(f"{label:>6} | {s['n_features']:>7d} | {s['n_rows']:>5d} | "
              f"{s['test_auc_mean']:.4f}   | {s['test_auc_std']:.4f} | "
              f"{s['test_auc_min']:.4f} | {s['test_auc_max']:.4f}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
