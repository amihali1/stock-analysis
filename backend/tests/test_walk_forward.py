"""Tests for the walk-forward backtest harness (P9-007)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.backtest.report import render_report, write_report
from src.backtest.walk_forward import _trade_pnl, walk_forward


def _synthetic_dataset(n_dates: int = 200, tickers: int = 3, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    start = date(2024, 1, 1)
    for d_idx in range(n_dates):
        for t_idx in range(tickers):
            f0 = rng.normal()
            f1 = rng.normal()
            # Label leans on f0 so the model has signal to learn
            label = 1 if (f0 + 0.4 * rng.normal()) > 0.3 else 0
            rows.append({
                "ticker": f"T{t_idx}",
                "date": start + timedelta(days=d_idx),
                "f0": f0, "f1": f1,
                "label": label,
            })
    return pd.DataFrame(rows)


def test_trade_pnl_thresholds():
    assert _trade_pnl(0.4, 1, threshold=0.5) is None
    assert _trade_pnl(0.6, 1, threshold=0.5) == 1.0
    assert _trade_pnl(0.6, 0, threshold=0.5) == -1.5


def test_walk_forward_runs_and_aggregates():
    df = _synthetic_dataset()
    result = walk_forward(
        df=df,
        feature_cols=["f0", "f1"],
        n_folds=3,
        train_min_rows=100,
        confidence_threshold=0.5,
    )
    assert len(result.folds) >= 2  # at least some folds completed
    assert "mean_auc" in result.aggregate
    assert result.aggregate["n_folds"] == len(result.folds)


def test_render_report_contains_sections():
    df = _synthetic_dataset()
    result = walk_forward(
        df=df, feature_cols=["f0", "f1"], n_folds=3, train_min_rows=100,
    )
    md = render_report(result, git_sha="abc1234",
                       old_metrics={"auc_roc": 0.50, "brier_score": 0.30, "hit_rate": 0.45})
    assert "Walk-forward backtest" in md
    assert "Aggregate metrics" in md
    assert "Per-fold detail" in md
    assert "Ship gate" in md
    assert "abc1234" in md
    assert "Old vs new" in md


def test_write_report_writes_file(tmp_path: Path):
    df = _synthetic_dataset()
    result = walk_forward(df=df, feature_cols=["f0", "f1"], n_folds=3, train_min_rows=100)
    out = write_report(result, tmp_path, git_sha="deadbeef")
    assert out.exists()
    assert "deadbeef" in out.name


def test_empty_df_raises():
    with pytest.raises(ValueError):
        walk_forward(df=pd.DataFrame(columns=["ticker", "date", "label"]),
                     feature_cols=["f0"], n_folds=2, train_min_rows=10)
