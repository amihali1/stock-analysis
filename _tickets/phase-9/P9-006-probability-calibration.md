# P9-006: Probability calibration for directional model

**Status**: done
**Phase**: 9
**Dependencies**: none (but most useful after P9-001, P9-002 land)
**Estimated scope**: small (1-2 files)

## Description
XGBoost raw probabilities are not well-calibrated — `predict_proba` outputs cluster around 0.5 and rarely reach 0.75+, which is why `directional_confidence = abs(prob - 0.5) * 2` almost never clears the 0.75 gate even when the model is correct more than it's wrong.

Wrap the trained booster in `CalibratedClassifierCV` (isotonic or Platt) so predicted probabilities reflect empirical frequencies. This alone should push the `dir_conf ≥ 0.75` hit rate from ~10% of days into a usable range.

Part of the Tier 1 model-quality bundle (P9-001 + P9-002 + P9-006). Costs one extra CV pass at training; inference-time cost is negligible.

## Acceptance Criteria
- [ ] Training script: after XGBoost fits, wrap with `CalibratedClassifierCV(cv='prefit', method='isotonic')` using held-out calibration fold (critical: can't use same data that fit the booster)
- [ ] Three-way split during training: train / calibration / test (e.g., 70/15/15, time-ordered)
- [ ] Model registry saves both the raw booster AND the calibrated wrapper; `predict_proba` at inference uses the calibrated version
- [ ] Reliability diagram generated on test set and saved next to model artifact (matplotlib plot, brier score as scalar metric)
- [ ] Brier score logged to `model_metadata` table alongside AUC
- [ ] Tests: calibrated model's probabilities cover a wider range than raw, predicted ≈ empirical on test bins
- [ ] Document expected impact: calibration rarely moves AUC, but it dramatically improves confidence-gate behavior

## Files to Create/Modify
- `backend/src/models/directional.py` (training + prediction path)
- `backend/scripts/train_directional.py` (or wherever training entry point lives)
- `backend/src/models/calibration_plot.py` (new helper)
- `backend/tests/test_directional_calibration.py` (new)

## Notes
- Isotonic is non-parametric and handles sigmoidal miscalibration well; prefer it over Platt unless train set is < a few thousand rows.
- `method='sigmoid'` (Platt) is the fallback for small data.
- `cv='prefit'` requires the calibration set be disjoint from the training set — respect time ordering.
- If this lands without P9-001/P9-002, expect AUC unchanged and gate hit rate up. If it lands after them, expect AUC up AND gate hit rate up.
