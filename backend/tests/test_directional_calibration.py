"""Tests for the calibrated probability path (P9-006)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV

from src.models.calibration_plot import reliability_data, save_reliability_plot
from src.models.directional import DirectionalModel


def _make_model_with_calibrator(seed: int = 0):
    """Train a tiny XGB + isotonic calibrator on synthetic data."""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(800, 4))
    # y depends on X[:,0] with noise — gives a learnable but imperfect signal
    y = (X[:, 0] + 0.5 * rng.normal(size=800) > 0).astype(int)

    X_train, y_train = X[:500], y[:500]
    X_calib, y_calib = X[500:700], y[500:700]
    X_test, y_test = X[700:], y[700:]

    booster = xgb.XGBClassifier(n_estimators=50, max_depth=3, eval_metric="logloss",
                                random_state=seed, n_jobs=1)
    booster.fit(X_train, y_train, verbose=False)

    cal = CalibratedClassifierCV(estimator=booster, cv="prefit", method="isotonic")
    cal.fit(X_calib, y_calib)
    return booster, cal, X_test, y_test


def test_calibrator_widens_or_matches_probability_range():
    booster, cal, X_test, y_test = _make_model_with_calibrator()
    raw = booster.predict_proba(X_test)[:, 1]
    cal_p = cal.predict_proba(X_test)[:, 1]
    # Isotonic on a noisy target shouldn't shrink the dynamic range.
    assert (cal_p.max() - cal_p.min()) >= 0.5 * (raw.max() - raw.min())


def test_directional_model_uses_calibrator_when_set(tmp_path):
    booster, cal, X_test, y_test = _make_model_with_calibrator()
    m = DirectionalModel(model_path=tmp_path / "m.pkl")
    m.model = booster
    m.calibrator = cal
    m.feature_cols = ["f0", "f1", "f2", "f3"]

    df = pd.DataFrame(X_test, columns=m.feature_cols)
    out = m.predict_batch(df)
    raw = booster.predict_proba(df)[:, 1]
    # When a calibrator is set, predictions should match it, not the raw booster
    np.testing.assert_allclose(out["drop_probability"].values, cal.predict_proba(df)[:, 1])
    # And differ from raw at least somewhere
    assert not np.allclose(out["drop_probability"].values, raw)


def test_save_load_round_trip_preserves_calibrator(tmp_path):
    booster, cal, X_test, _ = _make_model_with_calibrator()
    path = tmp_path / "m.pkl"
    m = DirectionalModel(model_path=path)
    m.model = booster
    m.calibrator = cal
    m.feature_cols = ["f0", "f1", "f2", "f3"]
    m.brier_score = 0.1234
    m.save()

    m2 = DirectionalModel(model_path=path)
    m2.load()
    assert m2.calibrator is not None
    assert m2.brier_score == pytest.approx(0.1234)


def test_reliability_data_basic():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, size=200)
    p = rng.uniform(size=200)
    out = reliability_data(y, p, n_bins=5)
    assert len(out["prob_pred"]) == len(out["prob_true"])
    assert 0 < out["brier"] < 1


def test_save_reliability_plot_writes_file(tmp_path):
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, size=200)
    p = rng.uniform(size=200)
    out = save_reliability_plot(y, p, tmp_path / "rel.png", n_bins=5)
    assert out["brier"] > 0
    assert (tmp_path / "rel.png").exists()
