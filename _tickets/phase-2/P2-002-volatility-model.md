# P2-002: Train volatility predictor (options plays)

**Status**: done
**Phase**: 2
**Dependencies**: P0-004
**Estimated scope**: large

## Description
Train a PyTorch LSTM to predict 5-day realized volatility. Compare predicted vol vs implied vol from options chains to identify mispriced options.

## Acceptance Criteria
- [ ] LSTM model defined in `models/volatility.py`
- [ ] 60-day lookback window as input sequence
- [ ] Features: historical vol, close returns, volume, RSI, VIX (from FRED)
- [ ] Target: realized volatility over next 5 trading days
- [ ] TIME-BASED split for train/val/test
- [ ] Training runs on homelab GPU (CUDA)
- [ ] Model serialized to `trained_models/volatility_lstm_v1.pt`
- [ ] Inference method: `predict(sequence: np.ndarray) -> float` returns predicted vol
- [ ] MAE reported and logged
- [ ] Update `_memory/MODEL_REGISTRY.md`

## Files to Create/Modify
- `backend/src/models/volatility.py`
- `backend/notebooks/volatility_exploration.ipynb`
- `backend/trained_models/volatility_lstm_v1.pt`
- `_memory/MODEL_REGISTRY.md`

## Notes
Train on homelab GPU via SSH/Jupyter. The 2070 Super handles this model size easily. If VRAM is tight, reduce batch size or hidden dims.
