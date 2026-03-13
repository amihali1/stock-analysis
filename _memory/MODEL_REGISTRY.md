# Model Registry

Track trained models, their hyperparameters, and performance metrics.

## Format

```
### Model Name (version)
- **Type**: algorithm
- **Trained**: date
- **Dataset**: description
- **Hyperparams**: key params
- **Metrics**: accuracy, precision, recall, etc.
- **File**: path to serialized model
- **Notes**: anything notable
```

---

### Directional XGBoost v1
- **Type**: XGBoost binary classifier
- **Trained**: 2026-03-13
- **Dataset**: 14,847 samples from 49 tickers × 2y, 17.0% positive rate
- **Target**: Stock drops >3% in next 5 trading days
- **Hyperparams**: n_estimators=200, max_depth=5, lr=0.05, subsample=0.8, colsample_bytree=0.8
- **Validation**: 3-fold walk-forward
- **Test metrics**: acc=0.633, prec=0.178, rec=0.400, f1=0.247, AUC=0.549
- **Top features**: sma_crossover (0.072), volatility_20d (0.072), bb_lower (0.071), sma_200 (0.069)
- **File**: `trained_models/directional_xgb_v1.pkl`
- **Notes**: Beats random baseline. Low precision expected — using as signal input to ensemble, not standalone.

---

### Volatility LSTM v1
- **Type**: PyTorch LSTM (2 layers, 64 hidden, dropout=0.2)
- **Trained**: 2026-03-13
- **Dataset**: 19,551 sequences (60-day lookback), 49 tickers × 2y
- **Target**: 5-day realized volatility (annualized)
- **Features**: close_return, volume_norm, rsi_14, volatility_hist
- **Split**: 70/15/15 time-based
- **Test metrics**: MAE=0.1085, RMSE=0.1662, Corr=0.3264
- **File**: `trained_models/volatility_lstm_v1.pt`
- **Notes**: Trained on CPU (~50s). Early stopped at epoch 11. Positive correlation with realized vol.
