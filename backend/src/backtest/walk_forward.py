"""Walk-forward backtest harness for the directional model (P9-007).

A single 80/20 split overstates performance on time-series data. This harness
slides a (train_window, test_window) pair forward through history, training a
fresh model on each slice and recording per-trade outcomes on the held-out side.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterable

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import brier_score_loss, roc_auc_score

from src.models.directional import DirectionalModel, build_dataset

logger = logging.getLogger(__name__)


@dataclass
class FoldResult:
    fold: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    n_train: int
    n_test: int
    auc: float
    brier: float
    hit_rate: float
    avg_pnl_per_trade: float
    n_trades: int


@dataclass
class BacktestResult:
    folds: list[FoldResult] = field(default_factory=list)
    aggregate: dict[str, float] = field(default_factory=dict)
    feature_cols: list[str] = field(default_factory=list)


def _fit_xgb(X_train: pd.DataFrame, y_train: pd.Series) -> xgb.XGBClassifier:
    pos_weight = len(y_train[y_train == 0]) / max(len(y_train[y_train == 1]), 1)
    model = xgb.XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=pos_weight, eval_metric="logloss",
        random_state=42, n_jobs=-1,
    )
    model.fit(X_train, y_train, verbose=False)
    return model


def _trade_pnl(prob: float, label: int, threshold: float, payoff_win: float = 1.0,
               payoff_loss: float = -1.5) -> float | None:
    """Convert a (prob, label) pair into a normalized trade P&L.

    `prob >= threshold` triggers a "drop" trade; payoff is +1.0 unit if the
    label is 1 (drop happened) else -1.5 (asymmetric to mimic real-trade R:R).
    Returns None if no trade was triggered.
    """
    if prob < threshold:
        return None
    return payoff_win if label == 1 else payoff_loss


def walk_forward(
    df: pd.DataFrame | None = None,
    feature_cols: list[str] | None = None,
    n_folds: int = 4,
    train_min_rows: int = 1000,
    confidence_threshold: float = 0.5,
    fit_fn: Callable[[pd.DataFrame, pd.Series], xgb.XGBClassifier] | None = None,
) -> BacktestResult:
    """Run a walk-forward backtest. Returns a `BacktestResult`.

    `df` defaults to the full dataset returned by `build_dataset()`. Pass a
    pre-built DataFrame in tests to avoid hitting the database.
    """
    if df is None:
        df = build_dataset()
    if df.empty:
        raise ValueError("Empty dataset; nothing to backtest")

    feature_cols = feature_cols or DirectionalModel().feature_cols
    fit_fn = fit_fn or _fit_xgb

    df = df.sort_values("date").reset_index(drop=True)
    dates = sorted(df["date"].unique())
    if len(dates) < n_folds + 1:
        raise ValueError(f"Need ≥{n_folds + 1} unique dates; have {len(dates)}")

    fold_size = len(dates) // (n_folds + 1)
    result = BacktestResult(feature_cols=list(feature_cols))

    for fold in range(n_folds):
        train_end = (fold + 1) * fold_size
        test_end = min(train_end + fold_size, len(dates))
        train_dates = dates[:train_end]
        test_dates = dates[train_end:test_end]
        if not test_dates:
            continue

        train_df = df[df["date"].isin(train_dates)]
        test_df = df[df["date"].isin(test_dates)]
        if len(train_df) < train_min_rows or test_df.empty:
            logger.warning("Fold %d skipped (train=%d, test=%d)", fold + 1, len(train_df), len(test_df))
            continue

        X_train = train_df[feature_cols]
        y_train = train_df["label"]
        X_test = test_df[feature_cols]
        y_test = test_df["label"].to_numpy()

        model = fit_fn(X_train, y_train)
        y_prob = model.predict_proba(X_test)[:, 1]

        auc = roc_auc_score(y_test, y_prob) if len(set(y_test)) > 1 else float("nan")
        brier = brier_score_loss(y_test, y_prob) if len(set(y_test)) > 1 else float("nan")

        pnls = [
            _trade_pnl(p, lab, confidence_threshold)
            for p, lab in zip(y_prob, y_test)
        ]
        traded = [p for p in pnls if p is not None]
        wins = [p for p in traded if p > 0]
        hit_rate = (len(wins) / len(traded)) if traded else 0.0
        avg_pnl = float(np.mean(traded)) if traded else 0.0

        fr = FoldResult(
            fold=fold + 1,
            train_start=train_dates[0], train_end=train_dates[-1],
            test_start=test_dates[0], test_end=test_dates[-1],
            n_train=len(train_df), n_test=len(test_df),
            auc=float(auc) if not np.isnan(auc) else 0.0,
            brier=float(brier) if not np.isnan(brier) else 0.0,
            hit_rate=hit_rate, avg_pnl_per_trade=avg_pnl, n_trades=len(traded),
        )
        result.folds.append(fr)
        logger.info(
            "Fold %d: AUC=%.3f Brier=%.4f trades=%d hit=%.2f%% avg_pnl=%.3f",
            fr.fold, fr.auc, fr.brier, fr.n_trades, fr.hit_rate * 100, fr.avg_pnl_per_trade,
        )

    if not result.folds:
        raise ValueError("No folds completed — check train_min_rows vs dataset size")

    aucs = [f.auc for f in result.folds]
    briers = [f.brier for f in result.folds if f.brier > 0]
    hits = [f.hit_rate for f in result.folds if f.n_trades > 0]
    pnls = [f.avg_pnl_per_trade for f in result.folds if f.n_trades > 0]
    result.aggregate = {
        "mean_auc": float(np.mean(aucs)),
        "mean_brier": float(np.mean(briers)) if briers else 0.0,
        "mean_hit_rate": float(np.mean(hits)) if hits else 0.0,
        "mean_avg_pnl": float(np.mean(pnls)) if pnls else 0.0,
        "total_trades": int(sum(f.n_trades for f in result.folds)),
        "n_folds": len(result.folds),
    }
    return result
