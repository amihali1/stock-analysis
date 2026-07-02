"""Inference vol_20d must match training formula.

Training: g["close"].pct_change().rolling(20).std() * sqrt(252).
Inference (scheduler, backtester) calls annualized_vol_20d on the last 21
closes sorted newest-first. Drift here is a hard train/serve skew — the
0.2 placeholder used previously meant every prediction had a constant
where training had real volatility.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.models.directional import annualized_vol_20d


def _reference_vol(closes_asc: list[float]) -> float:
    s = pd.Series(closes_asc)
    return float((s.pct_change().rolling(20).std() * np.sqrt(252)).iloc[-1])


def test_matches_pandas_pct_change_rolling_std_formula():
    rng = np.random.default_rng(42)
    closes_asc = (100.0 + rng.standard_normal(21).cumsum()).tolist()
    expected = _reference_vol(closes_asc)
    got = annualized_vol_20d(list(reversed(closes_asc)))
    assert got == pytest.approx(expected, rel=1e-12)


def test_uses_only_most_recent_21_closes():
    rng = np.random.default_rng(7)
    long_asc = (100.0 + rng.standard_normal(60).cumsum()).tolist()
    expected = _reference_vol(long_asc[-21:])
    got = annualized_vol_20d(list(reversed(long_asc)))
    assert got == pytest.approx(expected, rel=1e-12)


def test_short_history_returns_fallback():
    closes_desc = [100.0, 99.5, 99.0]
    assert annualized_vol_20d(closes_desc) == 0.2


def test_exactly_20_closes_is_short():
    closes_desc = [100.0 + i for i in range(20)]
    assert annualized_vol_20d(closes_desc) == 0.2


def test_zero_prior_close_returns_fallback():
    closes_desc = [100.0] * 20 + [0.0]
    assert annualized_vol_20d(closes_desc) == 0.2


def test_flat_prices_give_zero_vol():
    closes_desc = [100.0] * 21
    assert annualized_vol_20d(closes_desc) == pytest.approx(0.0, abs=1e-12)


def test_none_close_in_window_returns_fallback():
    # Regression: DB rows can carry NULL closes (partial-day yfinance data,
    # 2026-06-09 poisoned 157 tickers). Must fall back, not TypeError.
    closes_desc = [100.0] * 10 + [None] + [100.0] * 10
    assert annualized_vol_20d(closes_desc) == 0.2


def test_nan_close_in_window_returns_fallback():
    closes_desc = [100.0] * 10 + [float("nan")] + [100.0] * 10
    assert annualized_vol_20d(closes_desc) == 0.2


def test_none_newest_close_returns_fallback():
    closes_desc = [None] + [100.0] * 20
    assert annualized_vol_20d(closes_desc) == 0.2
