"""PyTorch LSTM for predicting 5-day realized volatility."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from src.db.models import PriceHistory, TechnicalIndicator
from src.db.session import SessionLocal

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent.parent.parent / "trained_models"
DEFAULT_MODEL_PATH = MODEL_DIR / "volatility_lstm_v1.pt"

LOOKBACK = 60  # Days of history as input
FORWARD_DAYS = 5  # Predict vol over next N days
FEATURE_NAMES = ["close_return", "volume_norm", "rsi_14", "volatility_hist"]


class VolatilityLSTM(nn.Module):
    def __init__(self, input_size: int = 4, hidden_size: int = 64, num_layers: int = 2, dropout: float = 0.2):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.fc = nn.Sequential(
            nn.Linear(hidden_size, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        lstm_out, _ = self.lstm(x)
        last_hidden = lstm_out[:, -1, :]
        return self.fc(last_hidden).squeeze(-1)


class VolDataset(Dataset):
    def __init__(self, sequences: np.ndarray, targets: np.ndarray):
        self.sequences = torch.FloatTensor(sequences)
        self.targets = torch.FloatTensor(targets)

    def __len__(self):
        return len(self.targets)

    def __getitem__(self, idx):
        return self.sequences[idx], self.targets[idx]


class VolatilityModel:
    def __init__(self, model_path: Path | None = None):
        self.model: VolatilityLSTM | None = None
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.scaler_params: dict | None = None  # mean/std for feature normalization
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    def load(self) -> None:
        """Load trained model from disk."""
        checkpoint = torch.load(self.model_path, map_location=self.device, weights_only=False)
        self.model = VolatilityLSTM(**checkpoint["model_config"])
        self.model.load_state_dict(checkpoint["model_state"])
        self.model.to(self.device)
        self.model.eval()
        self.scaler_params = checkpoint.get("scaler_params")
        logger.info(f"Loaded volatility model from {self.model_path} (device={self.device})")

    def save(self, config: dict) -> None:
        """Save trained model to disk."""
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "model_state": self.model.state_dict(),
            "model_config": config,
            "scaler_params": self.scaler_params,
        }, self.model_path)
        logger.info(f"Saved volatility model to {self.model_path}")

    def predict(self, sequence: np.ndarray) -> float:
        """Predict 5-day realized volatility from a (LOOKBACK, n_features) array.

        Returns annualized volatility as a float.
        """
        if self.model is None:
            self.load()

        if self.scaler_params:
            sequence = (sequence - self.scaler_params["mean"]) / (self.scaler_params["std"] + 1e-8)

        x = torch.FloatTensor(sequence).unsqueeze(0).to(self.device)
        with torch.no_grad():
            pred = self.model(x).item()
        return max(pred, 0.0)  # Vol can't be negative

    def train(self, tickers: list[str] | None = None, epochs: int = 50, batch_size: int = 64, lr: float = 1e-3) -> dict:
        """Train the LSTM with time-based split."""
        logger.info("Building volatility dataset...")
        sequences, targets, dates = build_vol_dataset(tickers)

        if len(sequences) < 200:
            raise ValueError(f"Not enough data: {len(sequences)} samples (need 200+)")

        logger.info(f"Dataset: {len(sequences)} samples, sequence shape {sequences.shape}")

        # Time-based split: 70% train, 15% val, 15% test
        n = len(sequences)
        train_end = int(n * 0.7)
        val_end = int(n * 0.85)

        # Normalize features using train stats only
        train_seqs = sequences[:train_end]
        self.scaler_params = {
            "mean": train_seqs.reshape(-1, train_seqs.shape[-1]).mean(axis=0),
            "std": train_seqs.reshape(-1, train_seqs.shape[-1]).std(axis=0),
        }

        def normalize(data):
            return (data - self.scaler_params["mean"]) / (self.scaler_params["std"] + 1e-8)

        train_ds = VolDataset(normalize(sequences[:train_end]), targets[:train_end])
        val_ds = VolDataset(normalize(sequences[train_end:val_end]), targets[train_end:val_end])
        test_ds = VolDataset(normalize(sequences[val_end:]), targets[val_end:])

        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=batch_size)
        test_loader = DataLoader(test_ds, batch_size=batch_size)

        # Model setup
        model_config = {"input_size": sequences.shape[-1], "hidden_size": 64, "num_layers": 2, "dropout": 0.2}
        self.model = VolatilityLSTM(**model_config)
        self.model.to(self.device)

        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
        criterion = nn.MSELoss()

        best_val_loss = float("inf")
        best_state = None
        patience_counter = 0

        for epoch in range(epochs):
            # Train
            self.model.train()
            train_loss = 0.0
            for X, y in train_loader:
                X, y = X.to(self.device), y.to(self.device)
                optimizer.zero_grad()
                pred = self.model(X)
                loss = criterion(pred, y)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                optimizer.step()
                train_loss += loss.item() * len(y)
            train_loss /= len(train_ds)

            # Validate
            self.model.eval()
            val_loss = 0.0
            with torch.no_grad():
                for X, y in val_loader:
                    X, y = X.to(self.device), y.to(self.device)
                    pred = self.model(X)
                    val_loss += criterion(pred, y).item() * len(y)
            val_loss /= len(val_ds)

            scheduler.step(val_loss)

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state = {k: v.cpu().clone() for k, v in self.model.state_dict().items()}
                patience_counter = 0
            else:
                patience_counter += 1

            if (epoch + 1) % 10 == 0:
                logger.info(f"Epoch {epoch + 1}/{epochs}: train_loss={train_loss:.6f} val_loss={val_loss:.6f}")

            if patience_counter >= 10:
                logger.info(f"Early stopping at epoch {epoch + 1}")
                break

        # Load best model
        self.model.load_state_dict(best_state)
        self.model.eval()

        # Test evaluation
        test_preds, test_targets = [], []
        with torch.no_grad():
            for X, y in test_loader:
                X = X.to(self.device)
                pred = self.model(X)
                test_preds.extend(pred.cpu().numpy())
                test_targets.extend(y.numpy())

        test_preds = np.array(test_preds)
        test_targets = np.array(test_targets)

        mae = np.mean(np.abs(test_preds - test_targets))
        rmse = np.sqrt(np.mean((test_preds - test_targets) ** 2))
        correlation = np.corrcoef(test_preds, test_targets)[0, 1] if len(test_preds) > 1 else 0.0

        logger.info(f"Test MAE={mae:.4f} RMSE={rmse:.4f} Corr={correlation:.4f}")

        self.save(model_config)

        return {
            "train_size": len(train_ds),
            "val_size": len(val_ds),
            "test_size": len(test_ds),
            "best_val_loss": best_val_loss,
            "test_mae": float(mae),
            "test_rmse": float(rmse),
            "test_correlation": float(correlation),
            "device": str(self.device),
        }


def build_vol_dataset(tickers: list[str] | None = None) -> tuple[np.ndarray, np.ndarray, list]:
    """Build sequences for volatility prediction.

    Returns (sequences, targets, dates) where:
    - sequences: (N, LOOKBACK, n_features) array
    - targets: (N,) array of 5-day realized vol (annualized)
    - dates: list of end dates for each sequence
    """
    db = SessionLocal()
    try:
        query = db.query(
            PriceHistory.ticker,
            PriceHistory.date,
            PriceHistory.close,
            PriceHistory.volume,
            TechnicalIndicator.rsi_14,
        ).join(
            TechnicalIndicator,
            (PriceHistory.ticker == TechnicalIndicator.ticker)
            & (PriceHistory.date == TechnicalIndicator.date),
        )

        if tickers:
            query = query.filter(PriceHistory.ticker.in_(tickers))

        rows = query.order_by(PriceHistory.ticker, PriceHistory.date).all()
        df = pd.DataFrame(rows, columns=["ticker", "date", "close", "volume", "rsi_14"])
    finally:
        db.close()

    all_sequences = []
    all_targets = []
    all_dates = []

    for ticker, group in df.groupby("ticker"):
        g = group.sort_values("date").reset_index(drop=True)

        if len(g) < LOOKBACK + FORWARD_DAYS + 10:
            continue

        # Compute features
        g["close_return"] = g["close"].pct_change()
        g["volume_norm"] = g["volume"] / g["volume"].rolling(20).mean()
        g["rsi_14"] = g["rsi_14"] / 100.0  # Normalize to 0-1
        g["volatility_hist"] = g["close_return"].rolling(20).std() * np.sqrt(252)

        # Target: forward 5-day realized vol (annualized)
        g["target_vol"] = g["close_return"].rolling(FORWARD_DAYS).std().shift(-FORWARD_DAYS) * np.sqrt(252)

        g = g.dropna().reset_index(drop=True)

        features = g[FEATURE_NAMES].values

        for i in range(len(g) - LOOKBACK - FORWARD_DAYS):
            seq = features[i : i + LOOKBACK]
            target = g["target_vol"].iloc[i + LOOKBACK]

            if np.isnan(seq).any() or np.isnan(target):
                continue

            all_sequences.append(seq)
            all_targets.append(target)
            all_dates.append(g["date"].iloc[i + LOOKBACK])

    # Sort by date for time-based splitting
    sort_idx = np.argsort(all_dates)
    sequences = np.array(all_sequences)[sort_idx]
    targets = np.array(all_targets)[sort_idx]
    dates = [all_dates[i] for i in sort_idx]

    return sequences, targets, dates


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    model = VolatilityModel()
    results = model.train()

    print(f"\nDataset: {results['train_size']} train, {results['val_size']} val, {results['test_size']} test")
    print(f"Device: {results['device']}")
    print(f"Test MAE:  {results['test_mae']:.4f}")
    print(f"Test RMSE: {results['test_rmse']:.4f}")
    print(f"Test Corr: {results['test_correlation']:.4f}")
