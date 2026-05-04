"""Recommendation candidate selection (extracted from scheduler for testability).

Replaces the legacy hardcoded `score >= 0.5` gate that produced zero recs because
the directional model is a calibrated rare-event classifier — its `dir_prob`
distribution clusters around the ~17.5% base rate, and the composite score
weights (0.4 * dir_prob + 0.3 * vol + 0.3 * sent) make a 0.5 cutoff effectively
require dir_prob > 0.7, a quarterly-frequency event.

New gating model: `dir_prob` is the gate (must beat base rate by some lift),
the composite score is the ranker (top-K by score among gate-passers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.models.ensemble import EnsembleScore


@dataclass
class Candidate:
    ticker: str
    score: EnsembleScore
    directional_prob: float
    # Free-form extras the scheduler needs after selection (price, indicator row, etc.)
    extras: dict[str, Any] = field(default_factory=dict)


def select_candidates(
    candidates: list[Candidate],
    base_rate: float,
    min_dir_prob_lift: float,
    top_k: int,
) -> list[Candidate]:
    """Filter on dir_prob >= base_rate * lift, sort by composite score desc, cap at top_k.

    `meets_confidence` is *not* applied here — keep that as a separate per-rec
    quality check in the caller so we can still log the filter rate.
    """
    if top_k <= 0:
        return []
    floor = base_rate * min_dir_prob_lift
    passing = [c for c in candidates if c.directional_prob >= floor]
    passing.sort(key=lambda c: c.score.score, reverse=True)
    return passing[:top_k]
