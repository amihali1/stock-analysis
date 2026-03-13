# P2-001: Train directional classifier (short candidates)

**Status**: todo
**Phase**: 2
**Dependencies**: P0-004
**Estimated scope**: large

## Description
Train an XGBoost classifier to predict which stocks will drop >3% in the next 5 trading days. This is the primary signal for short recommendations.

## Acceptance Criteria
- [ ] Training script in `models/directional.py`
- [ ] Dataset builder: joins price_history + technical_indicators, creates binary label
- [ ] TIME-BASED train/val/test split (no random splits — see decision D006)
- [ ] Walk-forward validation with at least 3 folds
- [ ] Feature importance plot saved to `notebooks/`
- [ ] Model serialized to `trained_models/directional_xgb_v1.pkl`
- [ ] Test accuracy >52% (better than random)
- [ ] Prediction method: `predict(features: dict) -> tuple[float, float]` returns (probability, confidence)
- [ ] Update `_memory/MODEL_REGISTRY.md` with results

## Files to Create/Modify
- `backend/src/models/directional.py`
- `backend/notebooks/directional_exploration.ipynb`
- `backend/trained_models/directional_xgb_v1.pkl`
- `_memory/MODEL_REGISTRY.md`

## Notes
Start with a Jupyter notebook for exploration, then extract the training code into the module. Use 2 years of data. Key features: RSI, MACD histogram, BB %B, volume z-score, SMA crossover, 5-day return momentum.
