"""Tests for the recommendation candidate ranker (P10-002).

The ranker replaces the legacy hardcoded `score >= 0.5` gate. The new model:
`dir_prob` gates (must beat base_rate * lift), composite score ranks (top-K).
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
    assert select_candidates([], base_rate=0.175, min_dir_prob_lift=1.5, top_k=10) == []


def test_top_k_zero_returns_empty():
    cands = [_cand("AAPL", 0.5, 0.6)]
    assert select_candidates(cands, base_rate=0.175, min_dir_prob_lift=1.5, top_k=0) == []


def test_dir_prob_floor_filters_weak_candidates():
    # base_rate * lift = 0.175 * 1.5 = 0.2625
    cands = [
        _cand("WEAK1", 0.10, 0.50),  # below floor
        _cand("WEAK2", 0.20, 0.60),  # below floor
        _cand("PASS",  0.30, 0.55),  # passes floor
    ]
    out = select_candidates(cands, base_rate=0.175, min_dir_prob_lift=1.5, top_k=10)
    assert [c.ticker for c in out] == ["PASS"]


def test_floor_inclusive_at_exact_threshold():
    floor = 0.175 * 2.0
    cands = [_cand("EXACT", floor, 0.4)]
    out = select_candidates(cands, base_rate=0.175, min_dir_prob_lift=2.0, top_k=5)
    assert [c.ticker for c in out] == ["EXACT"]


def test_sorted_by_score_descending():
    cands = [
        _cand("LOW",  0.30, 0.40),
        _cand("HIGH", 0.30, 0.80),
        _cand("MID",  0.30, 0.60),
    ]
    out = select_candidates(cands, base_rate=0.175, min_dir_prob_lift=1.5, top_k=10)
    assert [c.ticker for c in out] == ["HIGH", "MID", "LOW"]


def test_top_k_caps_output():
    cands = [_cand(f"T{i}", 0.30, 0.5 + i * 0.01) for i in range(20)]
    out = select_candidates(cands, base_rate=0.175, min_dir_prob_lift=1.5, top_k=5)
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
    out = select_candidates(cands, base_rate=0.175, min_dir_prob_lift=1.5, top_k=10)
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
    out = select_candidates(cands, base_rate=0.175, min_dir_prob_lift=1.5, top_k=5)
    assert out[0].extras == {"price_close": 175.5, "extra": "preserved"}
