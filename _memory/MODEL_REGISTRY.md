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

---

### Phase 9 — Pending Directional v2 Retrain

**Status**: code path ready, retrain not yet executed (requires production DB on homelab VM)

The directional model on disk (`directional_xgb_v1.pkl`) was trained on 17 features. The codebase now expects **40 features** — the original 17 plus 23 new phase-9 columns:

- **Options IV (6)** — `iv_atm_30d`, `iv_atm_90d`, `iv_rank_252d`, `iv_percentile_252d`, `put_call_skew_25d`, `term_structure_slope` (+ `has_options` flag, not in feature list)
- **Macro regime (7)** — `vix_level`, `vix_percentile_252d`, `spy_drawdown_pct`, `spy_above_sma_50`, `spy_above_sma_200`, `spy_return_5d`, `spy_return_20d`
- **Sector relative-strength (4)** — `sector_return_5d`, `sector_return_20d`, `return_5d_vs_sector`, `return_20d_vs_sector`
- **Sentiment time-series (6)** — `sentiment_latest`, `sentiment_ma_7d`, `sentiment_ma_30d`, `sentiment_momentum`, `sentiment_zscore_30d`, `article_count_zscore_30d`
- **Earnings proximity (4)** — `days_to_earnings`, `days_since_earnings`, `earnings_within_3d`, `earnings_within_10d`

Until the v2 retrain runs, `predict()` substitutes neutral defaults from `_merged_defaults()` for these columns, so v1 still serves with its original 17-feature signal.

**v2 plan (matches P9-007 acceptance criteria)**:
- **Window**: 5 years (or yfinance max per ticker), drop tickers without sufficient history rather than padding zeros
- **Split**: time-ordered 70 train / 15 calibration / 15 test
- **Calibration**: `CalibratedClassifierCV(cv='prefit', method='isotonic')` if calib fold ≥ 1000 rows, else `method='sigmoid'`
- **Validation**: walk-forward backtest via `scripts/run_backtest.py --folds 4`
- **Ship gate**: AUC > 0.55 AND hit rate > 0.52 AND Brier improved vs v1
- **Old artifact**: archive to `trained_models/archive/<date>/directional_xgb_v1.pkl` (do not overwrite)
- **Metadata to record at v2 time**: training rows, positive rate, hyperparams, full test metrics (acc/prec/rec/f1/AUC/Brier), top-10 feature importances, walk-forward fold table from the backtest report
