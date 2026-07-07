"""Event-feature experiment: per-ticker price/volume shock features, drop side.

Hypothesis (directional_model_information_ceiling memo): drops cluster around
catalysts. The three failed additive-column experiments (short interest,
insider, 8-K) were sparse external LEVELS; EVENT_FEATURE_COLS are dense
event-shaped signals derived from price history alone (gaps, shocks, volume
spikes, range breaks).

Protocol (matches seed_sweep_rise_excess.py):
- One dataset build: direction=drop, label_mode=vol_normalized, K=1.75,
  feature_cols = FEATURE_COLS + EVENT_FEATURE_COLS (so dropna gives BOTH arms
  identical rows — fair comparison).
- 10 seeds x {base arm, event arm}: 3-fold expanding walk-forward val AUCs +
  70/15/15 train/calib/test with sigmoid calibration.
- Report per-arm mean/std/min/max for fold AUC, test AUC, test brier, plus
  event-feature importances from the event arm.

Output: trained_models/experiment_event_features.json

Usage (inside backend container):
    python scripts/experiment_event_features.py
"""

from __future__ import annotations

import json
import logging
import statistics
import sys
from pathlib import Path

import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models.directional import (  # noqa: E402
    DEFAULT_DROP_VOL_K,
    EVENT_FEATURE_COLS,
    FEATURE_COLS,
    LABEL_MODE_VOL_NORMALIZED,
    _resolve_model_dir,
    build_dataset,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("event-features")

SEEDS = list(range(10))
N_FOLDS = 3
ARMS = {
    "base": FEATURE_COLS,
    "events": FEATURE_COLS + EVENT_FEATURE_COLS,
}


def _xgb(seed: int, y_tr) -> xgb.XGBClassifier:
    return xgb.XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=len(y_tr[y_tr == 0]) / max(len(y_tr[y_tr == 1]), 1),
        eval_metric="logloss", random_state=seed, n_jobs=-1,
    )


def train_one(df, feature_cols: list[str], seed: int) -> dict:
    dates = sorted(df["date"].unique())
    fold_size = len(dates) // (N_FOLDS + 1)

    fold_aucs = []
    for fold in range(N_FOLDS):
        train_end = (fold + 1) * fold_size
        val_end = train_end + fold_size
        train_mask = df["date"].isin(dates[:train_end])
        val_mask = df["date"].isin(dates[train_end:val_end])
        X_tr, y_tr = df.loc[train_mask, feature_cols], df.loc[train_mask, "label"]
        X_va, y_va = df.loc[val_mask, feature_cols], df.loc[val_mask, "label"]
        if len(X_va) == 0 or len(set(y_va)) < 2:
            continue
        model = _xgb(seed, y_tr)
        model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
        fold_aucs.append(float(roc_auc_score(y_va, model.predict_proba(X_va)[:, 1])))

    n_dates = len(dates)
    train_set = set(dates[: int(n_dates * 0.70)])
    calib_set = set(dates[int(n_dates * 0.70): int(n_dates * 0.85)])
    test_set = set(dates[int(n_dates * 0.85):])

    X_tr = df.loc[df["date"].isin(train_set), feature_cols]
    y_tr = df.loc[df["date"].isin(train_set), "label"]
    X_ca = df.loc[df["date"].isin(calib_set), feature_cols]
    y_ca = df.loc[df["date"].isin(calib_set), "label"]
    X_te = df.loc[df["date"].isin(test_set), feature_cols]
    y_te = df.loc[df["date"].isin(test_set), "label"]

    final = _xgb(seed, y_tr)
    final.fit(X_tr, y_tr, verbose=False)
    calib = CalibratedClassifierCV(final, method="sigmoid", cv="prefit")
    calib.fit(X_ca, y_ca)
    proba = calib.predict_proba(X_te)[:, 1]

    importances = dict(zip(feature_cols, (float(x) for x in final.feature_importances_)))
    return {
        "fold_aucs": fold_aucs,
        "test_auc": float(roc_auc_score(y_te, proba)),
        "test_brier": float(brier_score_loss(y_te, proba)),
        "event_importances": {k: v for k, v in importances.items() if k in EVENT_FEATURE_COLS},
    }


def summarize(vals: list[float]) -> dict:
    return {
        "mean": round(statistics.mean(vals), 4),
        "std": round(statistics.stdev(vals), 4) if len(vals) > 1 else 0.0,
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
    }


def main() -> int:
    logger.info("Building dataset (drop / vol_normalized / K=%.2f) with event cols...",
                DEFAULT_DROP_VOL_K)
    df = build_dataset(
        direction="drop",
        label_mode=LABEL_MODE_VOL_NORMALIZED,
        feature_cols=FEATURE_COLS + EVENT_FEATURE_COLS,
        vol_k=DEFAULT_DROP_VOL_K,
    )
    logger.info("Dataset: %d rows, %d tickers, pos_rate=%.4f",
                len(df), df["ticker"].nunique(), df["label"].mean())

    results: dict = {"dataset": {"rows": len(df), "pos_rate": round(float(df['label'].mean()), 4)}}
    for arm, cols in ARMS.items():
        runs = [train_one(df, cols, seed) for seed in SEEDS]
        all_folds = [a for r in runs for a in r["fold_aucs"]]
        results[arm] = {
            "test_auc": summarize([r["test_auc"] for r in runs]),
            "test_brier": summarize([r["test_brier"] for r in runs]),
            "fold_auc": summarize(all_folds),
        }
        if arm == "events":
            # Mean importance per event feature across seeds
            agg: dict[str, list[float]] = {}
            for r in runs:
                for k, v in r["event_importances"].items():
                    agg.setdefault(k, []).append(v)
            results[arm]["event_importances_mean"] = {
                k: round(statistics.mean(v), 5) for k, v in sorted(agg.items())
            }
        logger.info("%s: test_auc=%s fold_auc=%s", arm,
                    results[arm]["test_auc"], results[arm]["fold_auc"])

    delta = results["events"]["test_auc"]["mean"] - results["base"]["test_auc"]["mean"]
    results["delta_test_auc_mean"] = round(delta, 4)
    logger.info("DELTA test AUC (events - base): %+.4f", delta)

    out = _resolve_model_dir() / "experiment_event_features.json"
    out.write_text(json.dumps(results, indent=2))
    logger.info("Wrote %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
