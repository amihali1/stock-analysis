"""Tests for the recommendation candidate ranker.

Current architecture (post-2026-05-12 calibration plateau finding):
Pure top-K by composite score, no absolute dir_prob floor. The legacy
`dir_prob >= base_rate * lift` gate was a knife-edge on v3's isotonic
calibration plateaus — under sigmoid calibration, dir_prob values cluster
tightly around the base rate and absolute thresholds become noise.
"""

from __future__ import annotations

import pytest

from src.models.ensemble import EnsembleScore
from src.pipeline.rec_ranker import Candidate, select_candidates


def _cand(ticker: str, dir_prob: float, score: float, meets: bool = True) -> Candidate:
    return Candidate(
        ticker=ticker,
        score=EnsembleScore(
            ticker=ticker,
            score=score,
            directional_signal=dir_prob,
            volatility_signal=0.5,
            sentiment_signal=0.5,
            meets_confidence=meets,
        ),
        directional_prob=dir_prob,
    )


def test_empty_input_returns_empty():
    assert select_candidates([], top_k=10) == []


def test_top_k_zero_returns_empty():
    cands = [_cand("AAPL", 0.5, 0.6)]
    assert select_candidates(cands, top_k=0) == []


def test_low_dir_prob_candidates_not_filtered():
    # Under sigmoid calibration, dir_prob clusters around 0.18 (base rate).
    # The ranker must NOT filter on absolute dir_prob — composite score ranks.
    cands = [
        _cand("LOWDIR", 0.10, 0.80),  # very low dir_prob but high composite
        _cand("MIDDIR", 0.18, 0.50),
        _cand("HIGHDIR", 0.30, 0.40),
    ]
    out = select_candidates(cands, top_k=10)
    assert [c.ticker for c in out] == ["LOWDIR", "MIDDIR", "HIGHDIR"]


def test_sorted_by_score_descending():
    cands = [
        _cand("LOW",  0.30, 0.40),
        _cand("HIGH", 0.30, 0.80),
        _cand("MID",  0.30, 0.60),
    ]
    out = select_candidates(cands, top_k=10)
    assert [c.ticker for c in out] == ["HIGH", "MID", "LOW"]


def test_top_k_caps_output():
    cands = [_cand(f"T{i}", 0.30, 0.5 + i * 0.01) for i in range(20)]
    out = select_candidates(cands, top_k=5)
    assert len(out) == 5
    # Highest scores first
    assert out[0].score.score == pytest.approx(0.69)
    assert out[-1].score.score == pytest.approx(0.65)


def test_meets_confidence_does_not_filter():
    # The ranker is intentionally agnostic to meets_confidence — caller decides.
    cands = [
        _cand("UNCONF", 0.30, 0.70, meets=False),
        _cand("CONF",   0.30, 0.50, meets=True),
    ]
    out = select_candidates(cands, top_k=10)
    assert {c.ticker for c in out} == {"UNCONF", "CONF"}


def test_extras_passthrough():
    cands = [Candidate(
        ticker="AAPL",
        score=EnsembleScore(
            ticker="AAPL", score=0.6, directional_signal=0.4,
            volatility_signal=0.5, sentiment_signal=0.5,
        ),
        directional_prob=0.4,
        extras={"price_close": 175.5, "extra": "preserved"},
    )]
    out = select_candidates(cands, top_k=5)
    assert out[0].extras == {"price_close": 175.5, "extra": "preserved"}


def test_legacy_kwargs_accepted_but_ignored():
    # Callers that still pass base_rate/min_dir_prob_lift (e.g., during rollout)
    # should not error — the kwargs are accepted and ignored.
    cands = [_cand("WEAK", 0.05, 0.6)]
    out = select_candidates(cands, top_k=10, base_rate=0.175, min_dir_prob_lift=2.0)
    assert [c.ticker for c in out] == ["WEAK"]
