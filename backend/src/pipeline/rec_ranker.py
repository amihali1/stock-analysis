"""Recommendation candidate selection.

Phase 3 (bullish-side build, 2026-05-13): candidates are direction-tagged. The
original Phase 4 design joint-ranked across directions with cross-direction
per-ticker dedup ("keep higher score").

Phase 4 revision (2026-05-18): cross-direction dedup REMOVED. Drop v7
(vol_normalized label, K=1.75) ships drop_prob with base rate ~5% while rise
v2 (excess label) ships rise_prob with base rate ~17%. Composite scores for
the two sides are not on the same scale, so joint ranking gives bulls a
structural advantage and produces a drop-side dead zone (5 days of 0 short
recs after Phase 4 went live). Fix: rank within each direction independently,
take top_k per side, allow same ticker to emit both directions. The "$5,000
direction-blind capital cap" decision (bullish_side_build memo) is a sizing
concern, not a ranker concern — position sizing happens downstream.

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
    min_score: float | None = None,
    top_k_by_direction: dict[str, int] | None = None,
) -> list[Candidate]:
    """Rank candidates per direction, cap at top_k per direction.

    `top_k` is the cap **per direction**. A call with top_k=10 returns up to
    10 drop + 10 rise = 20 candidates. The two sides rank independently so
    the lower-base-rate drop probabilities don't get squeezed out by the
    higher-base-rate rise probabilities (see module docstring).

    Within a direction, same-ticker duplicates collapse to the higher score.

    Cross-direction CONFLICTS are excluded on both sides (2026-07-08): when a
    ticker lands in BOTH directions' top-K, the model is disagreeing with
    itself — the first pair_short morning emitted long INTC (bull_spread) and
    short INTC (pair) simultaneously, a self-hedged book. Per the project's
    "models must agree / skip marginal setups" rule the conflicted ticker is
    dropped from BOTH selections and each side backfills from its own ranked
    list. This is NOT the pre-2026-05-18 joint ranking (which starved drops
    via base-rate scale mismatch); each side still ranks independently.

    `top_k_by_direction` (optional) overrides the per-direction cap for the
    given directions (others fall back to `top_k`). Used by the regime-aware
    selection mix to take e.g. 7 with-tape / 3 counter-tape instead of an even
    split. A direction mapped to 0 is fully cut for that run.

    `min_score` (optional, in [0, 1]) is an absolute composite-score floor
    applied per candidate *before* the top-K cap so the ranker never returns
    picks below a minimum conviction (project's "skip marginal setups" rule).

    Output is sorted by composite score descending across both directions.
    No absolute `dir_prob` floor — see module docstring.
    """
    if top_k <= 0 or not candidates:
        return []

    best_by_dir_ticker: dict[tuple[str, str], Candidate] = {}
    for c in candidates:
        if min_score is not None and c.score.score < min_score:
            continue
        key = (c.direction, c.ticker)
        prev = best_by_dir_ticker.get(key)
        if prev is None or c.score.score > prev.score.score:
            best_by_dir_ticker[key] = c

    by_direction: dict[str, list[Candidate]] = {}
    for c in best_by_dir_ticker.values():
        by_direction.setdefault(c.direction, []).append(c)
    for cands in by_direction.values():
        cands.sort(key=lambda c: c.score.score, reverse=True)

    # Per-direction top-K with iterative cross-direction conflict exclusion:
    # banned tickers are removed from both sides and each side backfills from
    # its own ranked list; refills can create new conflicts, so loop until
    # stable. Terminates because `banned` strictly grows.
    banned: set[str] = set()
    while True:
        selections: dict[str, list[Candidate]] = {}
        for direction, cands in by_direction.items():
            cap = top_k if top_k_by_direction is None else top_k_by_direction.get(direction, top_k)
            selections[direction] = [c for c in cands if c.ticker not in banned][:cap]

        if len(selections) < 2:
            break
        ticker_sides: dict[str, int] = {}
        for sel in selections.values():
            for c in sel:
                ticker_sides[c.ticker] = ticker_sides.get(c.ticker, 0) + 1
        conflicts = {t for t, n in ticker_sides.items() if n > 1}
        if not conflicts:
            break
        banned |= conflicts

    selected: list[Candidate] = []
    for sel in selections.values():
        selected.extend(sel)
    selected.sort(key=lambda c: c.score.score, reverse=True)
    return selected
