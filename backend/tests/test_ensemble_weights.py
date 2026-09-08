"""Ensemble weighting — directional-only score (IC rebuild Phase 2)."""
from __future__ import annotations

import pytest

from src.models.ensemble import Ensemble, SignalInputs


def _inputs(**kw):
    base = dict(
        ticker="AAA",
        drop_prob=0.12,
        rise_prob=0.28,
        predicted_vol=0.6,
        sentiment_score=0.5,
        sentiment_confidence=0.9,
        current_price=100.0,
    )
    base.update(kw)
    return SignalInputs(**base)


def test_dir_only_score_equals_dir_prob():
    """With weights 1/0/0 the composite score IS the directional probability —
    vol and sentiment (which inverted the ranking) carry zero weight."""
    ens = Ensemble(weight_directional=1.0, weight_volatility=0.0, weight_sentiment=0.0)
    bear, bull = ens.score(_inputs())
    assert bull.direction == "rise"
    assert bull.score == pytest.approx(0.28)   # == rise_prob
    assert bear.direction == "drop"
    assert bear.score == pytest.approx(0.12)   # == drop_prob


def test_components_still_logged_under_dir_only():
    """Vol + sentiment must still be COMPUTED (for IC comparison) even at 0 weight."""
    ens = Ensemble(weight_directional=1.0, weight_volatility=0.0, weight_sentiment=0.0)
    _, bull = ens.score(_inputs())
    assert bull.volatility_signal > 0
    assert bull.sentiment_signal > 0


def test_default_settings_are_dir_only():
    """The shipped default (config weight_* = 1/0/0) must produce a dir-only score."""
    ens = Ensemble()  # picks up config defaults for base_rate etc, weights default 0.4/0.3/0.3
    # Explicit construction is what the scheduler now does; verify the config
    # defaults wire through to a dir-only score.
    from src.config import get_settings
    s = get_settings()
    ens2 = Ensemble(
        weight_directional=s.weight_directional,
        weight_volatility=s.weight_volatility,
        weight_sentiment=s.weight_sentiment,
    )
    _, bull = ens2.score(_inputs())
    assert bull.score == pytest.approx(0.28)


def test_legacy_weights_blend():
    """Old 0.4/0.3/0.3 still works (regression guard for the weight plumbing)."""
    ens = Ensemble(weight_directional=0.4, weight_volatility=0.3, weight_sentiment=0.3)
    _, bull = ens.score(_inputs())
    # 0.4*0.28 + 0.3*min(0.6,1) + 0.3*((1+0.5)/2*0.9)
    expected = 0.4 * 0.28 + 0.3 * 0.6 + 0.3 * ((1 + 0.5) / 2 * 0.9)
    assert bull.score == pytest.approx(round(expected, 4))
