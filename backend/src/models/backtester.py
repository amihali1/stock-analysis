"""Backtesting engine: replays historical signals and computes portfolio metrics."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.db.models import PriceHistory, TechnicalIndicator, SentimentScore
from src.db.session import SessionLocal
from src.models.directional import DirectionalModel, FEATURE_COLS, build_dataset
from src.models.ensemble import Ensemble, SignalInputs
from src.models.position_sizer import PositionSizer

logger = logging.getLogger(__name__)

# Default backtest parameters
DEFAULT_HOLD_DAYS = 5  # Hold period for each trade
MIN_SCORE_THRESHOLD = 0.5  # Minimum ensemble score to take a trade


@dataclass
class Trade:
    """A single backtest trade."""
    ticker: str
    strategy: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    position_size: float
    shares_or_contracts: int
    stop_loss: float
    target_price: float
    max_loss: float
    score: float
    pnl: float = 0.0
    return_pct: float = 0.0
    hit_stop: bool = False
    hit_target: bool = False


@dataclass
class BacktestResult:
    """Full backtest result with trades and metrics."""
    strategy: str  # "short", "options", "combined"
    start_date: date
    end_date: date
    trades: list[Trade] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    daily_equity: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "start_date": str(self.start_date),
            "end_date": str(self.end_date),
            "num_trades": len(self.trades),
            "metrics": self.metrics,
            "trades": [
                {
                    "ticker": t.ticker, "strategy": t.strategy,
                    "entry_date": str(t.entry_date), "exit_date": str(t.exit_date),
                    "entry_price": t.entry_price, "exit_price": t.exit_price,
                    "pnl": round(t.pnl, 2), "return_pct": round(t.return_pct, 4),
                    "score": round(t.score, 4), "hit_stop": t.hit_stop, "hit_target": t.hit_target,
                }
                for t in self.trades
            ],
            "daily_equity": self.daily_equity,
        }


class Backtester:
    """Replay model signals on historical data to evaluate strategy performance.

    Supports three modes:
    - short: short-selling only
    - options: put options only
    - combined: both strategies
    """

    def __init__(
        self,
        max_position: float = 1000.0,
        hold_days: int = DEFAULT_HOLD_DAYS,
        score_threshold: float = MIN_SCORE_THRESHOLD,
        max_concurrent_positions: int = 10,
    ):
        self.max_position = max_position
        self.hold_days = hold_days
        self.score_threshold = score_threshold
        self.max_concurrent = max_concurrent_positions
        self.ensemble = Ensemble()
        self.sizer = PositionSizer(max_position=max_position)

    def run(
        self,
        tickers: list[str] | None = None,
        strategy: str = "combined",
        start_date: date | None = None,
        end_date: date | None = None,
        retrain_interval_days: int = 0,
    ) -> BacktestResult:
        """Run a backtest over historical data.

        Args:
            tickers: Tickers to backtest. None = all available.
            strategy: "short", "options", or "combined"
            start_date: Start of backtest window. None = earliest available.
            end_date: End of backtest window. None = latest available.
            retrain_interval_days: If >0, retrain the directional model every N days (walk-forward).
        """
        logger.info(f"Loading historical data for backtest (strategy={strategy})...")
        price_data, indicator_data, sentiment_data = self._load_data(tickers, start_date, end_date)

        if price_data.empty:
            raise ValueError("No price data available for backtest")

        all_dates = sorted(price_data["date"].unique())
        if start_date:
            all_dates = [d for d in all_dates if d >= start_date]
        if end_date:
            all_dates = [d for d in all_dates if d <= end_date]

        if len(all_dates) < self.hold_days + 10:
            raise ValueError(f"Not enough dates for backtest: {len(all_dates)} (need {self.hold_days + 10}+)")

        actual_start = all_dates[0]
        actual_end = all_dates[-1]
        available_tickers = sorted(price_data["ticker"].unique())

        logger.info(
            f"Backtest: {actual_start} to {actual_end}, "
            f"{len(all_dates)} trading days, {len(available_tickers)} tickers"
        )

        # Load or train directional model
        dir_model = DirectionalModel()
        try:
            dir_model.load()
        except FileNotFoundError:
            logger.warning("No trained directional model found — using dummy predictions")
            dir_model = None

        trades: list[Trade] = []
        open_positions: list[dict] = []  # Track open positions
        daily_equity: list[dict] = []
        cumulative_pnl = 0.0
        last_retrain_date = None

        for i, current_date in enumerate(all_dates):
            # Walk-forward retraining
            if retrain_interval_days > 0 and dir_model is not None:
                if last_retrain_date is None or (current_date - last_retrain_date).days >= retrain_interval_days:
                    dir_model = self._retrain_model(price_data, indicator_data, current_date)
                    last_retrain_date = current_date

            # Check and close expired/stopped/targeted positions
            closed_trades, still_open = self._check_positions(
                open_positions, price_data, current_date
            )
            for t in closed_trades:
                cumulative_pnl += t.pnl
                trades.append(t)
            open_positions = still_open

            # Generate new signals if we have capacity
            if len(open_positions) < self.max_concurrent and i < len(all_dates) - self.hold_days:
                exit_date_idx = min(i + self.hold_days, len(all_dates) - 1)
                planned_exit = all_dates[exit_date_idx]

                new_positions = self._generate_signals(
                    available_tickers, current_date, planned_exit,
                    price_data, indicator_data, sentiment_data,
                    dir_model, strategy,
                    max_new=self.max_concurrent - len(open_positions),
                )
                open_positions.extend(new_positions)

            # Track daily equity
            unrealized = sum(
                self._unrealized_pnl(pos, price_data, current_date)
                for pos in open_positions
            )
            daily_equity.append({
                "date": str(current_date),
                "cumulative_pnl": round(cumulative_pnl, 2),
                "unrealized": round(unrealized, 2),
                "total_equity": round(cumulative_pnl + unrealized, 2),
                "open_positions": len(open_positions),
            })

        # Force-close any remaining positions at last available price
        for pos in open_positions:
            trade = self._close_position(pos, price_data, all_dates[-1], reason="backtest_end")
            if trade:
                cumulative_pnl += trade.pnl
                trades.append(trade)

        result = BacktestResult(
            strategy=strategy,
            start_date=actual_start,
            end_date=actual_end,
            trades=trades,
            daily_equity=daily_equity,
        )
        result.metrics = self._compute_metrics(trades, daily_equity)

        logger.info(
            f"Backtest complete: {len(trades)} trades, "
            f"P&L=${result.metrics.get('total_pnl', 0):.2f}, "
            f"Win rate={result.metrics.get('win_rate', 0):.1%}"
        )
        return result

    def _load_data(
        self,
        tickers: list[str] | None,
        start_date: date | None,
        end_date: date | None,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Load price, indicator, and sentiment data from the database."""
        db = SessionLocal()
        try:
            # Prices
            q = db.query(
                PriceHistory.ticker, PriceHistory.date,
                PriceHistory.open, PriceHistory.high, PriceHistory.low,
                PriceHistory.close, PriceHistory.volume,
            )
            if tickers:
                q = q.filter(PriceHistory.ticker.in_(tickers))
            if start_date:
                q = q.filter(PriceHistory.date >= start_date)
            if end_date:
                q = q.filter(PriceHistory.date <= end_date)

            prices = pd.DataFrame(
                q.all(),
                columns=["ticker", "date", "open", "high", "low", "close", "volume"],
            )

            # Indicators
            q = db.query(
                TechnicalIndicator.ticker, TechnicalIndicator.date,
                TechnicalIndicator.rsi_14, TechnicalIndicator.macd,
                TechnicalIndicator.macd_signal, TechnicalIndicator.macd_histogram,
                TechnicalIndicator.bb_percent_b, TechnicalIndicator.bb_upper,
                TechnicalIndicator.bb_lower, TechnicalIndicator.sma_50,
                TechnicalIndicator.sma_200, TechnicalIndicator.sma_crossover,
                TechnicalIndicator.volume_zscore,
            )
            if tickers:
                q = q.filter(TechnicalIndicator.ticker.in_(tickers))
            if start_date:
                q = q.filter(TechnicalIndicator.date >= start_date)
            if end_date:
                q = q.filter(TechnicalIndicator.date <= end_date)

            indicators = pd.DataFrame(
                q.all(),
                columns=[
                    "ticker", "date", "rsi_14", "macd", "macd_signal", "macd_histogram",
                    "bb_percent_b", "bb_upper", "bb_lower", "sma_50", "sma_200",
                    "sma_crossover", "volume_zscore",
                ],
            )

            # Sentiment (may be sparse)
            q = db.query(
                SentimentScore.ticker, SentimentScore.date,
                SentimentScore.sentiment, SentimentScore.confidence,
            )
            if tickers:
                q = q.filter(SentimentScore.ticker.in_(tickers))

            sent_rows = q.all()
            if sent_rows:
                sentiments = pd.DataFrame(sent_rows, columns=["ticker", "date", "sentiment", "confidence"])
            else:
                sentiments = pd.DataFrame(columns=["ticker", "date", "sentiment", "confidence"])

        finally:
            db.close()

        return prices, indicators, sentiments

    def _generate_signals(
        self,
        tickers: list[str],
        current_date: date,
        exit_date: date,
        price_data: pd.DataFrame,
        indicator_data: pd.DataFrame,
        sentiment_data: pd.DataFrame,
        dir_model: DirectionalModel | None,
        strategy: str,
        max_new: int,
    ) -> list[dict]:
        """Generate trading signals for the current date. Returns list of new position dicts."""
        candidates = []
        already_in = set()  # Avoid duplicate positions on same ticker

        for ticker in tickers:
            # Get current day's price
            price_row = price_data[
                (price_data["ticker"] == ticker) & (price_data["date"] == current_date)
            ]
            if price_row.empty:
                continue
            current_price = float(price_row.iloc[0]["close"])
            if current_price <= 0:
                continue

            # Get current day's indicators
            ind_row = indicator_data[
                (indicator_data["ticker"] == ticker) & (indicator_data["date"] == current_date)
            ]
            if ind_row.empty:
                continue
            ind = ind_row.iloc[0]

            # Lagged returns at N trading days back. Training uses
            # close.pct_change(N); inference must match or every prediction
            # gets a zero on three feature columns that carry real importance.
            ticker_history = price_data[
                (price_data["ticker"] == ticker) & (price_data["date"] <= current_date)
            ].sort_values("date", ascending=False)
            recent_closes = ticker_history["close"].tolist()

            def _return_lag(n: int) -> float:
                if len(recent_closes) <= n or not recent_closes[n]:
                    return 0.0
                return (recent_closes[0] - recent_closes[n]) / recent_closes[n]

            # Build directional prediction
            if dir_model is not None:
                features = {
                    "rsi_14": ind.get("rsi_14", 50) or 50,
                    "macd": ind.get("macd", 0) or 0,
                    "macd_signal": ind.get("macd_signal", 0) or 0,
                    "macd_histogram": ind.get("macd_histogram", 0) or 0,
                    "bb_percent_b": ind.get("bb_percent_b", 0.5) or 0.5,
                    "bb_upper": ind.get("bb_upper", current_price) or current_price,
                    "bb_lower": ind.get("bb_lower", current_price) or current_price,
                    "sma_50": ind.get("sma_50", current_price) or current_price,
                    "sma_200": ind.get("sma_200", current_price) or current_price,
                    "sma_crossover": ind.get("sma_crossover", 0) or 0,
                    "volume_zscore": ind.get("volume_zscore", 0) or 0,
                    "return_5d_lag": _return_lag(5),
                    "return_10d_lag": _return_lag(10),
                    "return_20d_lag": _return_lag(20),
                    "close_to_sma50_ratio": current_price / (ind.get("sma_50", current_price) or current_price),
                    "close_to_sma200_ratio": current_price / (ind.get("sma_200", current_price) or current_price),
                    "volatility_20d": 0.2,
                }
                try:
                    dir_prob, dir_conf = dir_model.predict(features)
                except Exception:
                    dir_prob, dir_conf = 0.5, 0.0
            else:
                dir_prob, dir_conf = 0.5, 0.0

            # Get sentiment
            sent_rows = sentiment_data[
                (sentiment_data["ticker"] == ticker) & (sentiment_data["date"] <= current_date)
            ]
            if not sent_rows.empty:
                recent_sent = sent_rows.tail(10)
                sent_score = float(recent_sent["sentiment"].mean())
                sent_conf = float(recent_sent["confidence"].mean())
            else:
                sent_score, sent_conf = 0.0, 0.0

            inputs = SignalInputs(
                ticker=ticker,
                drop_prob=dir_prob,
                rise_prob=0.0,  # backtester is bearish-only; rise branch is unused
                predicted_vol=0.25,
                sentiment_score=sent_score,
                sentiment_confidence=sent_conf,
                current_price=current_price,
            )
            scores = self.ensemble.score(inputs)
            score = next(s for s in scores if s.direction == "drop")

            if score.score >= self.score_threshold:
                candidates.append((score, current_price, exit_date))

        # Sort by score descending, take top N
        candidates.sort(key=lambda x: x[0].score, reverse=True)

        new_positions = []
        for score, current_price, exit_d in candidates[:max_new]:
            if score.ticker in already_in:
                continue

            strategies_to_run = []
            if strategy in ("short", "combined"):
                strategies_to_run.append("short")
            if strategy in ("options", "combined"):
                strategies_to_run.append("options")

            for strat in strategies_to_run:
                if strat == "short":
                    rec = self.sizer.size_short(score, current_price)
                    if rec:
                        new_positions.append({
                            "ticker": score.ticker,
                            "strategy": "short",
                            "entry_date": current_date,
                            "exit_date": exit_d,
                            "entry_price": current_price,
                            "shares": rec.shares,
                            "stop_loss": rec.stop_loss,
                            "target_price": rec.target_price,
                            "position_size": rec.position_size,
                            "max_loss": rec.max_loss,
                            "score": score.score,
                        })
                else:
                    rec = self.sizer.size_options(score, current_price)
                    if rec:
                        new_positions.append({
                            "ticker": score.ticker,
                            "strategy": "options",
                            "entry_date": current_date,
                            "exit_date": exit_d,
                            "entry_price": current_price,
                            "contracts": rec.contracts,
                            "stop_loss": current_price * 1.05,
                            "target_price": rec.strike,
                            "position_size": rec.position_size,
                            "max_loss": rec.max_loss,
                            "score": score.score,
                        })

            already_in.add(score.ticker)

        return new_positions

    def _check_positions(
        self,
        open_positions: list[dict],
        price_data: pd.DataFrame,
        current_date: date,
    ) -> tuple[list[Trade], list[dict]]:
        """Check open positions for exits (expiry, stop-loss, target hit)."""
        closed = []
        still_open = []

        for pos in open_positions:
            # Check if position has expired
            if current_date >= pos["exit_date"]:
                trade = self._close_position(pos, price_data, current_date, reason="expiry")
                if trade:
                    closed.append(trade)
                continue

            # Check stop-loss and target
            price_row = price_data[
                (price_data["ticker"] == pos["ticker"]) & (price_data["date"] == current_date)
            ]
            if price_row.empty:
                still_open.append(pos)
                continue

            high = float(price_row.iloc[0]["high"])
            low = float(price_row.iloc[0]["low"])

            if pos["strategy"] == "short":
                # Stop-loss hit if price goes above stop
                if high >= pos["stop_loss"]:
                    trade = self._close_position(pos, price_data, current_date, reason="stop_loss", forced_price=pos["stop_loss"])
                    if trade:
                        trade.hit_stop = True
                        closed.append(trade)
                    continue
                # Target hit if price goes below target
                if low <= pos["target_price"]:
                    trade = self._close_position(pos, price_data, current_date, reason="target", forced_price=pos["target_price"])
                    if trade:
                        trade.hit_target = True
                        closed.append(trade)
                    continue
            else:
                # Options: stop-loss if underlying rises above stop
                if high >= pos["stop_loss"]:
                    trade = self._close_position(pos, price_data, current_date, reason="stop_loss", forced_price=pos["stop_loss"])
                    if trade:
                        trade.hit_stop = True
                        closed.append(trade)
                    continue

            still_open.append(pos)

        return closed, still_open

    def _close_position(
        self,
        pos: dict,
        price_data: pd.DataFrame,
        close_date: date,
        reason: str = "expiry",
        forced_price: float | None = None,
    ) -> Trade | None:
        """Close a position and compute P&L."""
        if forced_price is not None:
            exit_price = forced_price
        else:
            price_row = price_data[
                (price_data["ticker"] == pos["ticker"]) & (price_data["date"] == close_date)
            ]
            if price_row.empty:
                # Try to find the closest prior date
                ticker_prices = price_data[price_data["ticker"] == pos["ticker"]]
                prior = ticker_prices[ticker_prices["date"] <= close_date]
                if prior.empty:
                    return None
                exit_price = float(prior.iloc[-1]["close"])
            else:
                exit_price = float(price_row.iloc[0]["close"])

        entry_price = pos["entry_price"]

        if pos["strategy"] == "short":
            shares = pos.get("shares", 1)
            # Short P&L: profit when price goes down
            pnl = shares * (entry_price - exit_price)
            return_pct = (entry_price - exit_price) / entry_price if entry_price > 0 else 0.0
        else:
            # Options P&L: simplified — profit = max(0, strike - exit_price) * 100 * contracts - premium
            contracts = pos.get("contracts", 1)
            strike = pos.get("target_price", entry_price * 0.95)
            premium_paid = pos["position_size"]  # Total premium = max loss for long puts
            intrinsic = max(0, strike - exit_price) * 100 * contracts
            pnl = intrinsic - premium_paid
            return_pct = pnl / premium_paid if premium_paid > 0 else 0.0

        return Trade(
            ticker=pos["ticker"],
            strategy=pos["strategy"],
            entry_date=pos["entry_date"],
            exit_date=close_date,
            entry_price=entry_price,
            exit_price=exit_price,
            position_size=pos["position_size"],
            shares_or_contracts=pos.get("shares", pos.get("contracts", 1)),
            stop_loss=pos["stop_loss"],
            target_price=pos["target_price"],
            max_loss=pos["max_loss"],
            score=pos["score"],
            pnl=round(pnl, 2),
            return_pct=round(return_pct, 4),
        )

    def _unrealized_pnl(self, pos: dict, price_data: pd.DataFrame, current_date: date) -> float:
        """Compute unrealized P&L for an open position."""
        price_row = price_data[
            (price_data["ticker"] == pos["ticker"]) & (price_data["date"] == current_date)
        ]
        if price_row.empty:
            return 0.0

        current_price = float(price_row.iloc[0]["close"])

        if pos["strategy"] == "short":
            shares = pos.get("shares", 1)
            return shares * (pos["entry_price"] - current_price)
        else:
            contracts = pos.get("contracts", 1)
            strike = pos.get("target_price", pos["entry_price"] * 0.95)
            intrinsic = max(0, strike - current_price) * 100 * contracts
            return intrinsic - pos["position_size"]

    def _retrain_model(
        self, price_data: pd.DataFrame, indicator_data: pd.DataFrame, cutoff_date: date
    ) -> DirectionalModel:
        """Retrain directional model using data up to cutoff_date (walk-forward)."""
        logger.info(f"Walk-forward retrain up to {cutoff_date}")
        try:
            model = DirectionalModel()
            model.train(n_folds=2)
            return model
        except Exception as e:
            logger.warning(f"Retrain failed: {e}, using existing model")
            model = DirectionalModel()
            try:
                model.load()
            except FileNotFoundError:
                pass
            return model

    def _compute_metrics(self, trades: list[Trade], daily_equity: list[dict]) -> dict:
        """Compute portfolio-level backtest metrics."""
        if not trades:
            return {
                "total_pnl": 0.0, "num_trades": 0, "win_rate": 0.0,
                "avg_pnl": 0.0, "avg_return": 0.0,
                "sharpe_ratio": 0.0, "max_drawdown": 0.0, "profit_factor": 0.0,
                "avg_hold_days": 0, "stop_loss_rate": 0.0, "target_hit_rate": 0.0,
            }

        pnls = [t.pnl for t in trades]
        returns = [t.return_pct for t in trades]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]

        total_pnl = sum(pnls)
        win_rate = len(winners) / len(trades) if trades else 0.0
        avg_pnl = np.mean(pnls)
        avg_return = np.mean(returns)

        # Sharpe ratio (annualized, assuming ~252 trading days)
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(252 / max(self.hold_days, 1))
        else:
            sharpe = 0.0

        # Max drawdown from daily equity curve
        max_drawdown = 0.0
        if daily_equity:
            equity_values = [d["total_equity"] for d in daily_equity]
            peak = equity_values[0]
            for v in equity_values:
                if v > peak:
                    peak = v
                dd = (peak - v) if peak > 0 else 0
                max_drawdown = max(max_drawdown, dd)

        # Profit factor = gross profit / gross loss
        gross_profit = sum(winners) if winners else 0.0
        gross_loss = abs(sum(losers)) if losers else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

        # Hold duration
        hold_days = []
        for t in trades:
            delta = (t.exit_date - t.entry_date).days
            hold_days.append(delta)

        stop_count = sum(1 for t in trades if t.hit_stop)
        target_count = sum(1 for t in trades if t.hit_target)

        return {
            "total_pnl": round(total_pnl, 2),
            "num_trades": len(trades),
            "win_rate": round(win_rate, 4),
            "avg_pnl": round(float(avg_pnl), 2),
            "avg_return": round(float(avg_return), 4),
            "sharpe_ratio": round(float(sharpe), 4),
            "max_drawdown": round(max_drawdown, 2),
            "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else "inf",
            "avg_hold_days": round(np.mean(hold_days), 1) if hold_days else 0,
            "stop_loss_rate": round(stop_count / len(trades), 4),
            "target_hit_rate": round(target_count / len(trades), 4),
            "best_trade": round(max(pnls), 2),
            "worst_trade": round(min(pnls), 2),
            "total_winners": len(winners),
            "total_losers": len(losers),
        }

    def compare_strategies(
        self,
        tickers: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        """Run backtests for all three strategies and return comparison."""
        results = {}
        for strategy in ("short", "options", "combined"):
            logger.info(f"Running {strategy} backtest...")
            result = self.run(
                tickers=tickers,
                strategy=strategy,
                start_date=start_date,
                end_date=end_date,
            )
            results[strategy] = result.to_dict()

        # Comparison summary
        comparison = {
            "strategies": results,
            "summary": {
                strategy: {
                    "total_pnl": r["metrics"]["total_pnl"],
                    "num_trades": r["metrics"]["num_trades"],
                    "win_rate": r["metrics"]["win_rate"],
                    "sharpe_ratio": r["metrics"]["sharpe_ratio"],
                    "max_drawdown": r["metrics"]["max_drawdown"],
                    "profit_factor": r["metrics"]["profit_factor"],
                }
                for strategy, r in results.items()
            },
        }
        return comparison


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    bt = Backtester()
    result = bt.run(strategy="combined")

    print(json.dumps(result.to_dict(), indent=2))
