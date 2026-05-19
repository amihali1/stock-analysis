"""Portfolio-level risk management: correlation, sector exposure, aggregate metrics."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.db.models import PaperTrade, PriceHistory, Stock, PortfolioSnapshot
from src.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_MAX_CORRELATION = 0.70
DEFAULT_MAX_SECTOR_PCT = 0.30
DEFAULT_MAX_POSITIONS = 20
CORRELATION_WINDOW = 60


class RiskManager:
    """Portfolio-level risk controls and metrics."""

    def __init__(
        self,
        db: Session,
        max_correlation: float = DEFAULT_MAX_CORRELATION,
        max_sector_pct: float = DEFAULT_MAX_SECTOR_PCT,
        max_positions: int = DEFAULT_MAX_POSITIONS,
    ):
        self.db = db
        self.max_correlation = max_correlation
        self.max_sector_pct = max_sector_pct
        self.max_positions = max_positions
        self.max_position_size = get_settings().effective_per_trade_cap

    def get_open_trades(self) -> list[PaperTrade]:
        return self.db.query(PaperTrade).filter_by(status="open").all()

    def compute_correlation_matrix(self, tickers: list[str], window: int = CORRELATION_WINDOW) -> dict:
        """Compute rolling correlation matrix from daily returns."""
        if len(tickers) < 2:
            return {"tickers": tickers, "matrix": [[1.0]] if tickers else [], "window": window}

        cutoff = date.today() - timedelta(days=window + 30)  # Buffer for data
        prices = {}

        for ticker in tickers:
            rows = (
                self.db.query(PriceHistory.date, PriceHistory.close)
                .filter(PriceHistory.ticker == ticker, PriceHistory.date >= cutoff)
                .order_by(PriceHistory.date)
                .all()
            )
            if rows:
                prices[ticker] = {r.date: r.close for r in rows if r.close}

        if len(prices) < 2:
            return {"tickers": tickers, "matrix": [[1.0] * len(tickers)] * len(tickers), "window": window}

        # Build DataFrame of daily returns
        df = pd.DataFrame(prices).sort_index()
        returns = df.pct_change().dropna()

        if len(returns) < 5:
            return {"tickers": list(prices.keys()), "matrix": [[1.0] * len(prices)] * len(prices), "window": window}

        # Use last `window` days
        returns = returns.tail(window)
        corr = returns.corr().fillna(0)

        ordered_tickers = list(corr.columns)
        matrix = corr.values.tolist()

        return {"tickers": ordered_tickers, "matrix": matrix, "window": window}

    def check_correlation_limit(self, new_ticker: str) -> tuple[bool, str]:
        """Check if adding a new position would exceed correlation limits.

        Returns (allowed, reason).
        """
        open_trades = self.get_open_trades()
        if not open_trades:
            return True, ""

        existing_tickers = list({t.ticker for t in open_trades})
        all_tickers = existing_tickers + [new_ticker]

        corr_data = self.compute_correlation_matrix(all_tickers)
        tickers = corr_data["tickers"]
        matrix = corr_data["matrix"]

        if new_ticker not in tickers:
            return True, ""

        new_idx = tickers.index(new_ticker)
        for i, ticker in enumerate(tickers):
            if ticker == new_ticker:
                continue
            if ticker in existing_tickers and abs(matrix[new_idx][i]) > self.max_correlation:
                return False, f"Correlation with {ticker} is {matrix[new_idx][i]:.2f} (limit: {self.max_correlation})"

        return True, ""

    def get_sector_exposure(self) -> dict:
        """Get current sector exposure from open positions."""
        open_trades = self.get_open_trades()
        if not open_trades:
            return {"sectors": {}, "total_exposure": 0, "max_sector_pct": self.max_sector_pct}

        sector_totals: dict[str, float] = {}
        total = 0.0

        for trade in open_trades:
            stock = self.db.query(Stock).filter_by(ticker=trade.ticker).first()
            sector = stock.sector if stock and stock.sector else "Unknown"
            amount = trade.position_size or 0
            sector_totals[sector] = sector_totals.get(sector, 0) + amount
            total += amount

        sectors = {}
        for sector, amount in sorted(sector_totals.items(), key=lambda x: -x[1]):
            pct = amount / total if total > 0 else 0
            sectors[sector] = {
                "amount": round(amount, 2),
                "percentage": round(pct, 4),
                "over_limit": pct > self.max_sector_pct,
            }

        return {"sectors": sectors, "total_exposure": round(total, 2), "max_sector_pct": self.max_sector_pct}

    def check_sector_limit(self, new_ticker: str, position_size: float) -> tuple[bool, str]:
        """Check if adding a position would breach sector limits."""
        stock = self.db.query(Stock).filter_by(ticker=new_ticker).first()
        sector = stock.sector if stock and stock.sector else "Unknown"

        exposure = self.get_sector_exposure()
        total = exposure["total_exposure"] + position_size
        current = exposure["sectors"].get(sector, {}).get("amount", 0)
        new_pct = (current + position_size) / total if total > 0 else 0

        if new_pct > self.max_sector_pct:
            return False, f"Sector '{sector}' would be {new_pct:.0%} of portfolio (limit: {self.max_sector_pct:.0%})"
        return True, ""

    def check_position_limit(self) -> tuple[bool, str]:
        """Check if we've hit the max open positions."""
        count = self.db.query(PaperTrade).filter_by(status="open").count()
        if count >= self.max_positions:
            return False, f"At position limit: {count}/{self.max_positions}"
        return True, ""

    def can_open_position(self, ticker: str, position_size: float) -> tuple[bool, list[str]]:
        """Run all risk checks for opening a new position.

        Returns (allowed, list_of_reasons_if_blocked).
        """
        reasons = []

        ok, reason = self.check_position_limit()
        if not ok:
            reasons.append(reason)

        ok, reason = self.check_correlation_limit(ticker)
        if not ok:
            reasons.append(reason)

        ok, reason = self.check_sector_limit(ticker, position_size)
        if not ok:
            reasons.append(reason)

        return len(reasons) == 0, reasons

    def compute_portfolio_metrics(self) -> dict:
        """Compute aggregate portfolio risk metrics."""
        open_trades = self.get_open_trades()

        if not open_trades:
            return {
                "total_exposure": 0,
                "total_max_loss": 0,
                "open_positions": 0,
                "max_positions": self.max_positions,
                "beta_to_spy": None,
                "tickers": [],
            }

        total_exposure = sum(t.position_size or 0 for t in open_trades)
        total_max_loss = sum(t.max_loss or 0 for t in open_trades)
        tickers = list({t.ticker for t in open_trades})

        # Beta to SPY
        beta = self._compute_portfolio_beta(open_trades)

        return {
            "total_exposure": round(total_exposure, 2),
            "total_max_loss": round(total_max_loss, 2),
            "open_positions": len(open_trades),
            "max_positions": self.max_positions,
            "beta_to_spy": round(beta, 4) if beta is not None else None,
            "tickers": tickers,
        }

    def _compute_portfolio_beta(self, trades: list[PaperTrade]) -> float | None:
        """Compute weighted portfolio beta relative to SPY."""
        cutoff = date.today() - timedelta(days=CORRELATION_WINDOW + 30)

        spy_rows = (
            self.db.query(PriceHistory.date, PriceHistory.close)
            .filter(PriceHistory.ticker == "SPY", PriceHistory.date >= cutoff)
            .order_by(PriceHistory.date)
            .all()
        )
        if len(spy_rows) < 10:
            return None

        spy_prices = {r.date: r.close for r in spy_rows if r.close}
        spy_df = pd.Series(spy_prices).sort_index()
        spy_returns = spy_df.pct_change().dropna()

        total_weight = sum(t.position_size or 0 for t in trades)
        if total_weight == 0:
            return None

        weighted_beta = 0.0
        for trade in trades:
            rows = (
                self.db.query(PriceHistory.date, PriceHistory.close)
                .filter(PriceHistory.ticker == trade.ticker, PriceHistory.date >= cutoff)
                .order_by(PriceHistory.date)
                .all()
            )
            if len(rows) < 10:
                continue

            ticker_prices = {r.date: r.close for r in rows if r.close}
            ticker_df = pd.Series(ticker_prices).sort_index()
            ticker_returns = ticker_df.pct_change().dropna()

            # Align dates
            common = spy_returns.index.intersection(ticker_returns.index)
            if len(common) < 10:
                continue

            s = spy_returns.loc[common].values
            t_r = ticker_returns.loc[common].values

            cov = np.cov(t_r, s)[0][1]
            var_spy = np.var(s)
            if var_spy == 0:
                continue

            beta = cov / var_spy
            weight = (trade.position_size or 0) / total_weight
            weighted_beta += beta * weight

        return weighted_beta

    def save_snapshot(self) -> None:
        """Save daily portfolio snapshot to DB."""
        metrics = self.compute_portfolio_metrics()
        today = date.today()

        existing = self.db.query(PortfolioSnapshot).filter_by(date=today).first()
        if existing:
            existing.total_exposure = metrics["total_exposure"]
            existing.total_max_loss = metrics["total_max_loss"]
            existing.open_positions = metrics["open_positions"]
            existing.beta_to_spy = metrics["beta_to_spy"]
        else:
            self.db.add(PortfolioSnapshot(
                date=today,
                total_exposure=metrics["total_exposure"],
                total_max_loss=metrics["total_max_loss"],
                open_positions=metrics["open_positions"],
                beta_to_spy=metrics["beta_to_spy"],
            ))
        self.db.commit()

    def get_full_risk_report(self) -> dict:
        """Generate complete risk report for the API."""
        metrics = self.compute_portfolio_metrics()
        sector = self.get_sector_exposure()
        tickers = metrics["tickers"]
        correlation = self.compute_correlation_matrix(tickers) if len(tickers) >= 2 else None

        return {
            "metrics": metrics,
            "sector_exposure": sector,
            "correlation": correlation,
        }
