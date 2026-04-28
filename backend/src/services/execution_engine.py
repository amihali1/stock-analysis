"""Automated execution engine — ties recommendations to Alpaca order submission."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import Recommendation, TradingLog
from src.services.alpaca_client import AlpacaClient
from src.services.order_mapper import OrderMapper
from src.services.safety_rails import TradingSafetyRails

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Orchestrates recommendation → order → submission flow."""

    def __init__(
        self,
        db: Session,
        alpaca: AlpacaClient | None = None,
        mapper: OrderMapper | None = None,
    ):
        self.db = db
        self.alpaca = alpaca or AlpacaClient()
        self.mapper = mapper or OrderMapper()
        self.settings = get_settings()

    def execute_recommendations(self) -> list[dict]:
        """Process today's recommendations through the full execution pipeline.

        1. Filter by score threshold
        2. Check safety rails
        3. Map to orders
        4. Submit to Alpaca
        5. Log results

        Returns list of execution results.
        """
        from src.services.trading_settings import get_trading_settings
        overrides = get_trading_settings(self.db, defaults=self.settings)
        if not overrides["auto_execute_enabled"]:
            logger.info("Auto-execution is disabled, skipping")
            return []

        today = date.today()
        recs = (
            self.db.query(Recommendation)
            .filter(
                Recommendation.date == today,
                Recommendation.score >= overrides["min_score_threshold"],
            )
            .order_by(Recommendation.score.desc())
            .all()
        )

        if not recs:
            logger.info("No eligible recommendations for execution")
            return []

        market_open = self.alpaca.is_market_open()
        rails = TradingSafetyRails(self.db)
        results = []

        for rec in recs:
            result = self._execute_single(rec, rails, market_open)
            results.append(result)

        logger.info(
            f"Execution complete: {sum(1 for r in results if r['status'] == 'submitted')}"
            f"/{len(results)} submitted"
        )
        return results

    def execute_recommendation_by_id(self, rec_id: int) -> dict:
        """Manually execute a single recommendation by ID."""
        rec = self.db.get(Recommendation, rec_id)
        if not rec:
            return {"status": "error", "reason": "Recommendation not found"}

        market_open = self.alpaca.is_market_open()
        rails = TradingSafetyRails(self.db)
        return self._execute_single(rec, rails, market_open)

    def _execute_single(
        self, rec: Recommendation, rails: TradingSafetyRails, market_open: bool,
    ) -> dict:
        """Execute a single recommendation through the pipeline."""
        # Get buying power
        try:
            account = self.alpaca.get_account()
            buying_power = account["buying_power"]
        except Exception:
            buying_power = None

        # Map recommendation to order params
        order_params = self.mapper.recommendation_to_order(
            ticker=rec.ticker,
            strategy=rec.strategy,
            entry_price=rec.entry_price,
            stop_loss=rec.stop_loss,
            target_price=rec.target_price,
            position_size=rec.position_size,
            contracts=rec.contracts,
            strike=rec.strike,
            option_type=rec.option_type,
            buying_power=buying_power,
        )

        if order_params is None:
            reason = f"Could not map recommendation {rec.id} to order"
            self._log(rec.ticker, "skip", rec.strategy, reason=reason)
            return {"rec_id": rec.id, "ticker": rec.ticker, "status": "skipped", "reason": reason}

        # Check safety rails
        allowed, rail_reason = rails.check_order(order_params, market_open=market_open)
        if not allowed:
            return {
                "rec_id": rec.id, "ticker": rec.ticker,
                "status": "blocked", "reason": rail_reason,
            }

        # Submit to Alpaca
        try:
            if order_params.is_bracket:
                result = self.alpaca.submit_bracket_order(
                    ticker=order_params.ticker,
                    qty=order_params.qty,
                    side=order_params.side,
                    limit_price=order_params.limit_price,
                    stop_loss_price=order_params.stop_loss_price,
                    take_profit_price=order_params.take_profit_price,
                    time_in_force=order_params.time_in_force,
                )
            else:
                result = self.alpaca.submit_order(
                    ticker=order_params.ticker,
                    qty=order_params.qty,
                    side=order_params.side,
                    order_type=order_params.order_type,
                    limit_price=order_params.limit_price,
                    time_in_force=order_params.time_in_force,
                )

            order_id = result["order_id"]
            rails.log_submission(order_params, order_id)
            self._send_alert(rec.ticker, "submitted", order_id, rec.strategy)

            return {
                "rec_id": rec.id, "ticker": rec.ticker,
                "status": "submitted", "order_id": order_id,
            }

        except Exception as e:
            reason = f"Alpaca submission failed: {e}"
            self._log(rec.ticker, "error", rec.strategy, reason=reason)
            return {"rec_id": rec.id, "ticker": rec.ticker, "status": "error", "reason": reason}

    def close_position(self, ticker: str) -> dict:
        """Close a specific position."""
        try:
            result = self.alpaca.close_position(ticker)
            self._log(ticker, "close", reason=f"Manual close")
            self._send_alert(ticker, "closed", result.get("order_id", ""))
            return result
        except Exception as e:
            return {"error": str(e)}

    def close_all_positions(self) -> dict:
        """Emergency liquidation — close all positions and cancel open orders."""
        try:
            cancel_result = self.alpaca.cancel_all_orders()
            close_results = self.alpaca.close_all_positions()
            self._log("ALL", "emergency_close", reason="Emergency liquidation")
            self._send_alert("ALL", "emergency_close", "")
            return {
                "canceled_orders": cancel_result.get("canceled", 0),
                "closing_positions": len(close_results),
            }
        except Exception as e:
            return {"error": str(e)}

    def get_execution_log(self, limit: int = 50) -> list[dict]:
        """Get recent execution history."""
        logs = (
            self.db.query(TradingLog)
            .order_by(TradingLog.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": log.id,
                "ticker": log.ticker,
                "action": log.action,
                "strategy": log.strategy,
                "qty": log.qty,
                "side": log.side,
                "order_id": log.order_id,
                "reason": log.reason,
                "passed_safety": bool(log.passed_safety),
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ]

    def _log(self, ticker: str, action: str, strategy: str = "", reason: str = ""):
        self.db.add(TradingLog(
            ticker=ticker,
            action=action,
            strategy=strategy,
            reason=reason,
            passed_safety=1 if action not in ("block", "error") else 0,
        ))
        self.db.commit()

    def _send_alert(self, ticker: str, event: str, order_id: str, strategy: str = ""):
        """Store an alert record for execution events."""
        try:
            from src.db.models import Alert
            message = f"[{event.upper()}] {ticker}"
            if strategy:
                message += f" ({strategy})"
            if order_id:
                message += f" — order {order_id}"
            self.db.add(Alert(
                ticker=ticker,
                alert_type="execution",
                message=message,
            ))
            self.db.commit()
        except Exception:
            logger.debug("Alert notification failed (non-critical)", exc_info=True)
