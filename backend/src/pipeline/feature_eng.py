"""Compute technical indicators from price history and store in technical_indicators table."""

import logging
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Stock, PriceHistory, TechnicalIndicator
from src.db.session import SessionLocal

logger = logging.getLogger(__name__)


class FeatureEngineer:
    def __init__(self, db: Session | None = None):
        self._owns_db = db is None
        self.db = db or SessionLocal()

    def close(self):
        if self._owns_db:
            self.db.close()

    def compute_all(self, tickers: list[str] | None = None) -> dict[str, int]:
        """Compute features for all tickers (or given list). Returns {ticker: rows_inserted}."""
        if tickers is None:
            tickers = [
                row[0] for row in self.db.execute(select(Stock.ticker)).all()
            ]

        results: dict[str, int] = {}
        for ticker in tickers:
            try:
                count = self.compute_features(ticker)
                results[ticker] = count
                logger.info(f"{ticker}: {count} new indicator rows")
            except Exception:
                logger.exception(f"Failed to compute features for {ticker}")
                results[ticker] = -1

        return results

    def compute_features(self, ticker: str) -> int:
        """Compute technical indicators for a single ticker. Returns rows inserted."""
        # Load price history sorted by date
        prices = (
            self.db.query(PriceHistory)
            .filter_by(ticker=ticker)
            .order_by(PriceHistory.date)
            .all()
        )

        if len(prices) < 30:
            logger.warning(f"{ticker}: only {len(prices)} price rows, need at least 30")
            return 0

        df = pd.DataFrame(
            {
                "date": [p.date for p in prices],
                "close": [p.close for p in prices],
                "high": [p.high for p in prices],
                "low": [p.low for p in prices],
                "volume": [p.volume for p in prices],
            }
        )
        df.set_index("date", inplace=True)

        # Compute all indicators
        df["rsi_14"] = _rsi(df["close"], 14)
        macd, signal, hist = _macd(df["close"], 12, 26, 9)
        df["macd"] = macd
        df["macd_signal"] = signal
        df["macd_histogram"] = hist
        upper, middle, lower, pct_b = _bollinger_bands(df["close"], 20, 2)
        df["bb_upper"] = upper
        df["bb_middle"] = middle
        df["bb_lower"] = lower
        df["bb_percent_b"] = pct_b
        df["sma_50"] = df["close"].rolling(50).mean()
        df["sma_200"] = df["close"].rolling(200).mean()
        df["sma_crossover"] = _sma_crossover(df["sma_50"], df["sma_200"])
        df["volume_zscore"] = _volume_zscore(df["volume"], 20)

        # Get existing indicator dates to avoid duplicates
        existing_dates = set(
            row[0]
            for row in self.db.execute(
                select(TechnicalIndicator.date).where(
                    TechnicalIndicator.ticker == ticker
                )
            ).all()
        )

        rows_inserted = 0
        for row_date, row in df.iterrows():
            if row_date in existing_dates:
                continue
            # Skip rows where RSI isn't computed yet (need 14+ bars)
            if pd.isna(row["rsi_14"]):
                continue

            indicator = TechnicalIndicator(
                ticker=ticker,
                date=row_date,
                rsi_14=_to_float(row["rsi_14"]),
                macd=_to_float(row["macd"]),
                macd_signal=_to_float(row["macd_signal"]),
                macd_histogram=_to_float(row["macd_histogram"]),
                bb_upper=_to_float(row["bb_upper"]),
                bb_middle=_to_float(row["bb_middle"]),
                bb_lower=_to_float(row["bb_lower"]),
                bb_percent_b=_to_float(row["bb_percent_b"]),
                sma_50=_to_float(row["sma_50"]),
                sma_200=_to_float(row["sma_200"]),
                sma_crossover=_to_float(row["sma_crossover"]),
                volume_zscore=_to_float(row["volume_zscore"]),
            )
            self.db.add(indicator)
            rows_inserted += 1

        if rows_inserted > 0:
            self.db.commit()

        return rows_inserted


# --- Indicator implementations ---


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, histogram."""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bollinger_bands(
    series: pd.Series, period: int = 20, std_dev: int = 2
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands: upper, middle, lower, %B."""
    middle = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    pct_b = (series - lower) / (upper - lower)
    return upper, middle, lower, pct_b


def _sma_crossover(sma_short: pd.Series, sma_long: pd.Series) -> pd.Series:
    """SMA crossover signal: 1.0 golden cross, -1.0 death cross, 0.0 neutral."""
    above = sma_short > sma_long
    prev_above = above.shift(1, fill_value=False)
    cross_up = above & ~prev_above
    cross_down = ~above & prev_above

    signal = pd.Series(0.0, index=sma_short.index)
    signal[cross_up] = 1.0
    signal[cross_down] = -1.0
    # Forward-fill the last crossover signal
    signal = signal.replace(0.0, np.nan).ffill().fillna(0.0)
    return signal


def _volume_zscore(volume: pd.Series, period: int = 20) -> pd.Series:
    """Volume z-score relative to rolling mean."""
    mean = volume.rolling(period).mean()
    std = volume.rolling(period).std()
    return (volume - mean) / std.replace(0, np.nan)


def _to_float(val) -> float | None:
    """Convert to float, None for NaN."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    try:
        f = float(val)
        return None if np.isnan(f) else round(f, 6)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from src.db.models import Base
    from src.db.session import engine
    from src.pipeline.data_fetcher import DataFetcher

    Base.metadata.create_all(engine)

    # Fetch some data first if needed
    fetcher = DataFetcher()
    test_tickers = ["AAPL", "MSFT", "SPY"]
    fetcher.fetch_daily(tickers=test_tickers, period="2y")
    fetcher.close()

    # Compute features
    eng = FeatureEngineer()
    results = eng.compute_all(tickers=test_tickers)
    for ticker, count in results.items():
        status = f"{count} rows" if count >= 0 else "FAILED"
        print(f"  {ticker}: {status}")
    eng.close()
