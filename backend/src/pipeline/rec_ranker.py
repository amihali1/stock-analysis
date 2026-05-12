"""Recommendation candidate selection.

History of the gate (chronological):

1. **Original (pre-P10-002):** Hardcoded `score >= 0.5` produced zero recs because
   the composite score weights (0.4*dir_prob + 0.3*vol + 0.3*sent) and a calibrated
   rare-event dir_prob (clustered near the ~17.5% base rate) made 0.5 effectively
   require dir_prob > 0.7 — a quarterly-frequency event.

2. **P10-002 (May 2026):** `dir_prob >= base_rate * lift` (default lift=1.3 → 0.2275)
   as a hard floor, with composite score as ranker. Worked when the v3 isotonic
   calibrator's plateau structure happened to land tickers in the 0.2277 bin.
   Broke catastrophically when it didn't — zero recs with no soft degradation.

3. **2026-05-12:** Investigation showed v3's isotonic calibrator emits ~4 discrete
   plateau values; the 0.2275 floor sat 0.0002 below one of them, making it a
   knife-edge gate. Re-fitted v3 with Platt sigmoid calibration — distribution
   smoothed to a tight 0.005-wide band around the 0.18 base rate.

**Current architecture (this file):** Pure top-K by composite score. No absolute
dir_prob floor. Rationale: under sigmoid calibration the model's outputs cluster
tightly around the base rate; absolute thresholds are noise. The composite score
(which integrates dir_prob with vol and sentiment, both of which DO vary
meaningfully across tickers) is the right ranking signal. Quality filtering
remains at the per-rec `meets_confidence` gate (sentiment-confidence floor only —
the directional-lift component was dropped for the same reason).
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
    top_k: int,
    base_rate: float | None = None,        # accepted for back-compat; ignored
    min_dir_prob_lift: float | None = None,  # accepted for back-compat; ignored
) -> list[Candidate]:
    """Rank candidates by composite score (descending), cap at top_k.

    No absolute `dir_prob` floor — see module docstring for why. `meets_confidence`
    is intentionally not applied here so the scheduler can log per-rec filter rates
    after ranking.

    The `base_rate` / `min_dir_prob_lift` kwargs are kept for back-compat with
    earlier callers (config-driven scheduler invocation). They are ignored.
    """
    if top_k <= 0 or not candidates:
        return []
    ranked = sorted(candidates, key=lambda c: c.score.score, reverse=True)
    return ranked[:top_k]
