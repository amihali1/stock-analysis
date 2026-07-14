# P11-001: Bull-side short-premium backtest (VRP harvest)

**Status**: done
**Phase**: 11
**Dependencies**: none (research-only; prod routing untouched)
**Estimated scope**: medium (research script + report)

## Description
The bull options book buys debit spreads — it PAYS the volatility risk
premium. IV persistently exceeds RV, and systematic option sellers (cash-
secured puts, put credit spreads, wheel) harvest that premium; it is the
most documented options edge in the literature (Quantpedia VRP, Monash
CQFIS, CBOE PUT index). `bull_put_credit_spread` already exists in
options_strategies.py but routing bypasses it.

Backtest expressing ML bull picks as short-premium entries instead of
debit spreads, using the joint top-K walk-forward harness pattern
(scripts/run_joint_topk_backtest.py, bear-monetization sweep 2026-07-07):

1. Arms: (a) current bull_call_debit_spread baseline, (b) bull_put_credit
   spread at same strikes discipline, (c) cash-secured put ~2-5% OTM,
   (d) plain long stock (money-layer sweep winner, for reference).
2. Same picks, same windows, same capital rules ($1k/trade, max_loss =
   collateral for credit structures).
3. Report expectancy/10d, win rate, max drawdown, left-tail (worst 5
   trades) per arm. Short-premium left tails are the known killer —
   drawdown/tail metrics are the decision criteria, not win rate (our own
   bear-side credit spreads had high win rates and NEGATIVE expectancy,
   sweep 2026-07-07).

## Acceptance Criteria
- [ ] Walk-forward backtest across all four arms on prod data
- [ ] Left-tail + drawdown reported per arm, not just means
- [ ] Written recommendation: keep debit spreads, switch to short premium, or route bulls to long stock
- [ ] No production routing changes in this ticket
