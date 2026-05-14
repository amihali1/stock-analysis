"""Experimental retrain of the directional drop/rise model with an excess-vs-SPY label.

Motivation (2026-05-14 root-cause audit): the absolute "fwd5_ret < -3%" / "> +3%"
label collides with macro regime mechanics — volatile regimes show 40% rise rate and
4% drop rate because by the time VIX spikes the drop already happened. The model
ends up learning a macro classifier (top 9 features are SPY/VIX/sector) with no
per-ticker discriminative power. Excess-vs-SPY removes that macro coupling:

    excess = ticker_fwd5_ret - spy_fwd5_ret
    drop_label = excess < -0.03   # "underperforms SPY by >3% in 5d"
    rise_label = excess >  0.03   # "outperforms SPY by >3% in 5d"

This is a one-shot script. It does NOT save models, does NOT touch trained_models/.
Output: a metrics JSON written to /tmp/excess_label_experiment.json with per-fold
AUC, test AUC, brier, feature importance — directly comparable to the v3 / v1
metrics files. Intended to be run inside the production backend container so it
shares the same Postgres + feature attachers as live.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, roc_auc_score

from src.db.models import PriceHistory, TechnicalIndicator
from src.db.session import SessionLocal
from src.features.analyst import attach_analyst_features
from src.features.earnings import attach_earnings_features
from src.features.insider import attach_insider_features
from src.features.macro import attach_macro_features
from src.features.sector import attach_sector_features
from src.features.short_interest import attach_short_interest_features
from src.features.wikipedia import attach_wikipedia_features
from src.models.directional import FEATURE_COLS, FORWARD_DAYS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("excess-experiment")

EXCESS_THRESHOLD = 0.03
SPY_TICKER = "SPY"


def build_excess_dataset(direction: str) -> pd.DataFrame:
    """Build feature frame with excess-vs-SPY label. Mirrors build_dataset
    structurally but rebuilds the label from forward returns + SPY forward returns.
    """
    db = SessionLocal()
    try:
        query = db.query(
            TechnicalIndicator.ticker,
            TechnicalIndicator.date,
            TechnicalIndicator.rsi_14,
            TechnicalIndicator.macd,
            TechnicalIndicator.macd_signal,
            TechnicalIndicator.macd_histogram,
            TechnicalIndicator.bb_percent_b,
            TechnicalIndicator.bb_upper,
            TechnicalIndicator.bb_lower,
            TechnicalIndicator.sma_50,
            TechnicalIndicator.sma_200,
            TechnicalIndicator.sma_crossover,
            TechnicalIndicator.volume_zscore,
            PriceHistory.close,
        ).join(
            PriceHistory,
            (TechnicalIndicator.ticker == PriceHistory.ticker)
            & (TechnicalIndicator.date == PriceHistory.date),
        )
        rows = query.all()
        df = pd.DataFrame(rows, columns=[
            "ticker", "date", "rsi_14", "macd", "macd_signal", "macd_histogram",
            "bb_percent_b", "bb_upper", "bb_lower", "sma_50", "sma_200",
            "sma_crossover", "volume_zscore", "close",
        ])

        # Fetch SPY forward returns separately so we can attach per-row.
        spy_rows = db.query(PriceHistory.date, PriceHistory.close).filter(
            PriceHistory.ticker == SPY_TICKER
        ).order_by(PriceHistory.date).all()
        spy_df = pd.DataFrame(spy_rows, columns=["date", "spy_close"])
        spy_df = spy_df.sort_values("date").reset_index(drop=True)
        spy_df["spy_fwd"] = spy_df["spy_close"].shift(-FORWARD_DAYS) / spy_df["spy_close"] - 1
    finally:
        db.close()

    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)

    all_ticker_dfs = []
    for ticker, group in df.groupby("ticker"):
        if ticker in (SPY_TICKER, "^VIX"):
            continue
        g = group.copy()
        g["return_5d_lag"] = g["close"].pct_change(5)
        g["return_10d_lag"] = g["close"].pct_change(10)
        g["return_20d_lag"] = g["close"].pct_change(20)
        g["close_to_sma50_ratio"] = g["close"] / g["sma_50"].replace(0, np.nan)
        g["close_to_sma200_ratio"] = g["close"] / g["sma_200"].replace(0, np.nan)
        g["volatility_20d"] = g["close"].pct_change().rolling(20).std() * np.sqrt(252)
        g["forward_return"] = g["close"].shift(-FORWARD_DAYS) / g["close"] - 1
        all_ticker_dfs.append(g)

    df = pd.concat(all_ticker_dfs, ignore_index=True)

    df = df.merge(spy_df[["date", "spy_fwd"]], on="date", how="left")
    df["excess"] = df["forward_return"] - df["spy_fwd"]

    if direction == "rise":
        df["label"] = (df["excess"] > EXCESS_THRESHOLD).astype(int)
    else:
        df["label"] = (df["excess"] < -EXCESS_THRESHOLD).astype(int)

    db = SessionLocal()
    try:
        df = attach_macro_features(db, df)
        df = attach_sector_features(db, df)
        df = attach_earnings_features(db, df)
        df = attach_analyst_features(db, df)
        df = attach_short_interest_features(db, df)
        df = attach_wikipedia_features(db, df)
        df = attach_insider_features(db, df)
    finally:
        db.close()

    feature_cols_with_label = FEATURE_COLS + ["label", "excess"]
    df = df.dropna(subset=feature_cols_with_label)

    keep_cols = ["ticker", "date"] + FEATURE_COLS + ["label", "excess"]
    return df[keep_cols]


def train_and_evaluate(direction: str, n_folds: int = 3) -> dict:
    logger.info("Building dataset (direction=%s, excess label)...", direction)
    df = build_excess_dataset(direction)
    if len(df) < 500:
        raise ValueError(f"Not enough rows: {len(df)}")
    pos_rate = float(df["label"].mean())
    logger.info("Rows=%d  positive_rate=%.4f", len(df), pos_rate)

    dates = sorted(df["date"].unique())
    fold_size = len(dates) // (n_folds + 1)

    fold_metrics = []
    for fold in range(n_folds):
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
            eval_metric="logloss", random_state=42, n_jobs=-1,
        )
        model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
        proba = model.predict_proba(X_va)[:, 1]
        auc = roc_auc_score(y_va, proba)
        fold_metrics.append({
            "fold": fold + 1,
            "train_size": int(len(X_tr)),
            "val_size": int(len(X_va)),
            "auc_roc": float(auc),
            "pos_rate": float(y_va.mean()),
        })
        logger.info("Fold %d: train=%d val=%d auc=%.4f pos=%.3f",
                    fold + 1, len(X_tr), len(X_va), auc, y_va.mean())

    n_dates = len(dates)
    train_end = int(n_dates * 0.70)
    calib_end = int(n_dates * 0.85)
    train_dates_final = set(dates[:train_end])
    calib_dates = set(dates[train_end:calib_end])
    test_dates_final = set(dates[calib_end:])

    X_tr = df.loc[df["date"].isin(train_dates_final), FEATURE_COLS]
    y_tr = df.loc[df["date"].isin(train_dates_final), "label"]
    X_ca = df.loc[df["date"].isin(calib_dates), FEATURE_COLS]
    y_ca = df.loc[df["date"].isin(calib_dates), "label"]
    X_te = df.loc[df["date"].isin(test_dates_final), FEATURE_COLS]
    y_te = df.loc[df["date"].isin(test_dates_final), "label"]

    final_model = xgb.XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=len(y_tr[y_tr == 0]) / max(len(y_tr[y_tr == 1]), 1),
        eval_metric="logloss", random_state=42, n_jobs=-1,
    )
    final_model.fit(X_tr, y_tr, verbose=False)

    calibrator = None
    if len(X_ca) >= 200 and len(set(y_ca)) > 1:
        calibrator = CalibratedClassifierCV(estimator=final_model, cv="prefit", method="sigmoid")
        calibrator.fit(X_ca, y_ca)

    proba_test = (calibrator if calibrator is not None else final_model).predict_proba(X_te)[:, 1]
    test_auc = roc_auc_score(y_te, proba_test) if len(set(y_te)) > 1 else 0.0
    brier = brier_score_loss(y_te, proba_test) if len(set(y_te)) > 1 else None

    importance = dict(zip(FEATURE_COLS, final_model.feature_importances_.tolist()))
    importance = dict(sorted(importance.items(), key=lambda kv: kv[1], reverse=True))

    return {
        "direction": direction,
        "label_def": f"excess > {EXCESS_THRESHOLD}" if direction == "rise" else f"excess < {-EXCESS_THRESHOLD}",
        "dataset_size": int(len(df)),
        "positive_rate": pos_rate,
        "fold_metrics": fold_metrics,
        "test_metrics": {
            "auc_roc": float(test_auc),
            "brier_score": float(brier) if brier is not None else None,
            "calibrated": calibrator is not None,
            "test_size": int(len(X_te)),
            "calib_size": int(len(X_ca)),
        },
        "feature_importance": {k: float(v) for k, v in importance.items()},
    }


def main() -> int:
    results = {}
    for direction in ("drop", "rise"):
        results[direction] = train_and_evaluate(direction, n_folds=3)
    out_path = "/tmp/excess_label_experiment.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "results": results,
        }, f, indent=2)
    logger.info("Wrote %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
