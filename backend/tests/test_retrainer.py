"""Retrainer champion/challenger gate.

Regression for 2026-07-05: the first scheduled retrain auto-deployed both
models because no champion metrics file existed — the gate compared the
challenger against auc_roc 0.0 / test_mae inf and trivially passed. An
unmeasured champion must fail CLOSED (keep current), not open.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import src.models.retrainer as retrainer_module
from src.models.retrainer import Retrainer


@pytest.fixture()
def model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(retrainer_module, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(retrainer_module, "DIR_MODEL_PATH", tmp_path / "directional_xgb_v1.pkl")
    monkeypatch.setattr(retrainer_module, "RISE_MODEL_PATH", tmp_path / "directional_xgb_rise_v2.pkl")
    monkeypatch.setattr(retrainer_module, "VOL_MODEL_PATH", tmp_path / "volatility_lstm_v1.pt")
    return tmp_path


def _mock_directional(auc: float, brier: float | None = 0.04, fold_aucs: list[float] | None = None):
    """Patch DirectionalModel so train() returns a challenger with given
    test-slice AUC / brier and optional walk-forward fold AUCs, and writes a
    fake pickle to its model_path."""
    def _factory(model_path, direction="drop", calibration_method=None):
        m = MagicMock()
        def _train(**kw):
            model_path.write_bytes(b"challenger")
            return {
                "test_metrics": {"auc_roc": auc, "brier_score": brier},
                "fold_metrics": [
                    {"fold": i + 1, "auc_roc": a} for i, a in enumerate(fold_aucs or [])
                ],
            }
        m.train = _train
        return m
    return patch.object(retrainer_module, "DirectionalModel", side_effect=_factory)


def _mock_volatility(mae: float):
    def _factory(model_path):
        m = MagicMock()
        def _train(**kw):
            model_path.write_bytes(b"challenger")
            return {"test_mae": mae, "test_rmse": mae * 1.3, "test_correlation": 0.5}
        m.train = _train
        return m
    return patch.object(retrainer_module, "VolatilityModel", side_effect=_factory)


def _seed_champion(model_dir, name: str, metrics: dict, model_file: str):
    (model_dir / f"{name}_metrics.json").write_text(json.dumps(metrics))
    (model_dir / model_file).write_bytes(b"champion")


class TestDirectionalGate:
    def test_no_champion_metrics_fails_closed(self, model_dir):
        (model_dir / "directional_xgb_v1.pkl").write_bytes(b"champion")

        with _mock_directional(auc=0.99):
            result = Retrainer().retrain_directional()

        assert result["deployed"] is False
        assert "no champion metrics" in result["status"]
        # Serving model untouched, challenger cleaned up, no metrics seeded
        assert (model_dir / "directional_xgb_v1.pkl").read_bytes() == b"champion"
        assert not (model_dir / "directional_metrics.json").exists()
        assert not list(model_dir.glob("directional_xgb_challenger_*.pkl"))

    def test_better_challenger_deploys(self, model_dir):
        _seed_champion(model_dir, "directional", {"auc_roc": 0.60}, "directional_xgb_v1.pkl")

        with _mock_directional(auc=0.65):
            result = Retrainer(improvement_threshold=0.01).retrain_directional()

        assert result["deployed"] is True
        assert (model_dir / "directional_xgb_v1.pkl").read_bytes() == b"challenger"
        saved = json.loads((model_dir / "directional_metrics.json").read_text())
        assert saved["auc_roc"] == 0.65
        # Old champion backed up
        assert list(model_dir.glob("directional_xgb_prev_*.pkl"))

    def test_worse_challenger_kept_out(self, model_dir):
        _seed_champion(model_dir, "directional", {"auc_roc": 0.60}, "directional_xgb_v1.pkl")

        with _mock_directional(auc=0.58):
            result = Retrainer(improvement_threshold=0.01).retrain_directional()

        assert result["deployed"] is False
        assert (model_dir / "directional_xgb_v1.pkl").read_bytes() == b"champion"
        assert json.loads((model_dir / "directional_metrics.json").read_text())["auc_roc"] == 0.60

    def test_marginal_improvement_below_threshold_kept_out(self, model_dir):
        _seed_champion(model_dir, "directional", {"auc_roc": 0.60}, "directional_xgb_v1.pkl")

        with _mock_directional(auc=0.605):
            result = Retrainer(improvement_threshold=0.01).retrain_directional()

        assert result["deployed"] is False


class TestWalkForwardGate:
    def test_walk_forward_primary_when_champion_has_it(self, model_dir):
        # Champion wf 0.60; challenger single-slice AUC is huge (0.75) but its
        # walk-forward mean (0.59) loses — walk-forward must decide.
        _seed_champion(
            model_dir, "directional",
            {"auc_roc": 0.64, "walk_forward_auc_mean": 0.60, "brier_score": 0.04},
            "directional_xgb_v1.pkl",
        )

        with _mock_directional(auc=0.75, fold_aucs=[0.58, 0.59, 0.60]):
            result = Retrainer(improvement_threshold=0.01).retrain_directional()

        assert result["deployed"] is False
        assert result["primary_metric"] == "walk_forward_auc_mean"
        assert (model_dir / "directional_xgb_v1.pkl").read_bytes() == b"champion"

    def test_walk_forward_win_deploys_and_persists_wf_fields(self, model_dir):
        _seed_champion(
            model_dir, "directional",
            {"auc_roc": 0.64, "walk_forward_auc_mean": 0.58, "brier_score": 0.04},
            "directional_xgb_v1.pkl",
        )

        with _mock_directional(auc=0.65, fold_aucs=[0.60, 0.61, 0.62]):
            result = Retrainer(improvement_threshold=0.01).retrain_directional()

        assert result["deployed"] is True
        saved = json.loads((model_dir / "directional_metrics.json").read_text())
        assert saved["walk_forward_auc_mean"] == pytest.approx(0.61)
        assert saved["walk_forward_folds"] == 3

    def test_legacy_champion_falls_back_to_test_auc(self, model_dir):
        # Champion predates walk-forward fields — single cycle compares on
        # auc_roc, and the deployment writes wf fields for the next cycle.
        _seed_champion(model_dir, "directional", {"auc_roc": 0.60}, "directional_xgb_v1.pkl")

        with _mock_directional(auc=0.65, fold_aucs=[0.62, 0.63, 0.64]):
            result = Retrainer(improvement_threshold=0.01).retrain_directional()

        assert result["deployed"] is True
        assert result["primary_metric"] == "auc_roc"
        saved = json.loads((model_dir / "directional_metrics.json").read_text())
        assert saved["walk_forward_auc_mean"] == pytest.approx(0.63)


class TestBrierGate:
    def test_brier_regression_blocks_deploy(self, model_dir):
        _seed_champion(
            model_dir, "directional",
            {"auc_roc": 0.60, "walk_forward_auc_mean": 0.58, "brier_score": 0.038},
            "directional_xgb_v1.pkl",
        )

        with _mock_directional(auc=0.70, brier=0.060, fold_aucs=[0.64, 0.65, 0.66]):
            result = Retrainer(improvement_threshold=0.01).retrain_directional()

        assert result["deployed"] is False
        assert "brier regression" in result["status"]
        assert (model_dir / "directional_xgb_v1.pkl").read_bytes() == b"champion"

    def test_brier_within_tolerance_deploys(self, model_dir):
        _seed_champion(
            model_dir, "directional",
            {"auc_roc": 0.60, "walk_forward_auc_mean": 0.58, "brier_score": 0.038},
            "directional_xgb_v1.pkl",
        )

        with _mock_directional(auc=0.70, brier=0.040, fold_aucs=[0.64, 0.65, 0.66]):
            result = Retrainer(improvement_threshold=0.01, brier_tolerance=0.005).retrain_directional()

        assert result["deployed"] is True

    def test_uncalibrated_challenger_blocked(self, model_dir):
        _seed_champion(
            model_dir, "directional",
            {"auc_roc": 0.60, "walk_forward_auc_mean": 0.58, "brier_score": 0.038},
            "directional_xgb_v1.pkl",
        )

        with _mock_directional(auc=0.70, brier=None, fold_aucs=[0.64, 0.65, 0.66]):
            result = Retrainer(improvement_threshold=0.01).retrain_directional()

        assert result["deployed"] is False
        assert "uncalibrated" in result["status"]


class TestRiseGate:
    def test_rise_uses_own_champion_files(self, model_dir):
        _seed_champion(
            model_dir, "directional_rise",
            {"auc_roc": 0.60, "walk_forward_auc_mean": 0.58, "brier_score": 0.15},
            "directional_xgb_rise_v2.pkl",
        )

        with _mock_directional(auc=0.65, brier=0.14, fold_aucs=[0.61, 0.62, 0.63]):
            result = Retrainer(improvement_threshold=0.01).retrain_rise()

        assert result["deployed"] is True
        assert result["model"] == "directional_rise"
        assert (model_dir / "directional_xgb_rise_v2.pkl").read_bytes() == b"challenger"
        # Drop-side champion files untouched
        assert not (model_dir / "directional_metrics.json").exists()
        saved = json.loads((model_dir / "directional_rise_metrics.json").read_text())
        assert saved["direction"] == "rise"
        assert list(model_dir.glob("directional_rise_xgb_prev_*.pkl"))

    def test_rise_no_champion_metrics_fails_closed(self, model_dir):
        (model_dir / "directional_xgb_rise_v2.pkl").write_bytes(b"champion")

        with _mock_directional(auc=0.99, fold_aucs=[0.9, 0.9, 0.9]):
            result = Retrainer().retrain_rise()

        assert result["deployed"] is False
        assert "no champion metrics" in result["status"]
        assert (model_dir / "directional_xgb_rise_v2.pkl").read_bytes() == b"champion"

    def test_retrain_all_covers_rise(self, model_dir):
        with _mock_directional(auc=0.65), _mock_volatility(mae=0.15):
            results = Retrainer().retrain_all()

        assert set(results.keys()) == {"directional", "directional_rise", "volatility"}


class TestVolatilityGate:
    def test_no_champion_metrics_fails_closed(self, model_dir):
        (model_dir / "volatility_lstm_v1.pt").write_bytes(b"champion")

        with _mock_volatility(mae=0.01):
            result = Retrainer().retrain_volatility()

        assert result["deployed"] is False
        assert "no champion metrics" in result["status"]
        assert (model_dir / "volatility_lstm_v1.pt").read_bytes() == b"champion"
        assert not (model_dir / "volatility_metrics.json").exists()

    def test_lower_mae_deploys(self, model_dir):
        _seed_champion(model_dir, "volatility", {"test_mae": 0.20}, "volatility_lstm_v1.pt")

        with _mock_volatility(mae=0.15):
            result = Retrainer(improvement_threshold=0.01).retrain_volatility()

        assert result["deployed"] is True
        assert (model_dir / "volatility_lstm_v1.pt").read_bytes() == b"challenger"

    def test_higher_mae_kept_out(self, model_dir):
        _seed_champion(model_dir, "volatility", {"test_mae": 0.20}, "volatility_lstm_v1.pt")

        with _mock_volatility(mae=0.25):
            result = Retrainer(improvement_threshold=0.01).retrain_volatility()

        assert result["deployed"] is False
        assert (model_dir / "volatility_lstm_v1.pt").read_bytes() == b"champion"
