"""Recommendation candidate selection.

Phase 3 (bullish-side build, 2026-05-13): candidates are now direction-tagged.
Each ticker can contribute up to two candidates (one drop, one rise). The ranker
dedups by ticker, keeping the higher-scoring direction, then applies top-K.

Prior history of the bearish-only gate (still relevant for the composite-score
rationale): the original `score >= 0.5` floor produced zero recs because
calibrated dir_prob clusters near the base rate. P10-002 swapped in a relative
`dir_prob >= base_rate * lift` gate, which became a knife-edge on v3's isotonic
calibration plateaus. 2026-05-12 sigmoid recalibration eliminated the plateaus
but also collapsed dir_prob's discriminative power across tickers — the composite
score (which integrates vol + sentiment) is the only meaningful ranking signal.

Quality filtering moved to the scheduler — this module is purely a ranker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.models.ensemble import EnsembleScore


@dataclass
class Candidate:
    ticker: str
    score: EnsembleScore
    direction: str  # "drop" (bearish) or "rise" (bullish) — mirrors score.direction
    # Free-form extras the scheduler needs after selection (price, indicator row, etc.)
    extras: dict[str, Any] = field(default_factory=dict)


def select_candidates(
    candidates: list[Candidate],
    top_k: int,
    base_rate: float | None = None,        # accepted for back-compat; ignored
    min_dir_prob_lift: float | None = None,  # accepted for back-compat; ignored
) -> list[Candidate]:
    """Rank candidates by composite score (descending), cap at top_k.

    When a ticker appears in both directions, keep only the higher-scoring side.
    This enforces the user's direction-blind capital cap (bullish_side_build memo):
    bullish and bearish candidates compete for the same top-K slots, and only one
    direction-of-conviction wins per ticker.

    No absolute `dir_prob` floor — see module docstring. `meets_confidence` is
    intentionally not applied here so the scheduler can log per-rec filter rates.
    """
    if top_k <= 0 or not candidates:
        return []
    best_by_ticker: dict[str, Candidate] = {}
    for c in candidates:
        prev = best_by_ticker.get(c.ticker)
        if prev is None or c.score.score > prev.score.score:
            best_by_ticker[c.ticker] = c
    ranked = sorted(best_by_ticker.values(), key=lambda c: c.score.score, reverse=True)
    return ranked[:top_k]
