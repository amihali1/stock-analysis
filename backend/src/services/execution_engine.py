"""Automated execution engine — ties recommendations to Alpaca order submission."""

from __future__ import annotations

import logging
from datetime import date, datetime, time

from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import PaperTrade, Recommendation, TradingLog
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
        # Per-direction score floors: drop composites run structurally lower
        # than rise composites (5% vs 17% label base rate) — a direction-blind
        # floor silently excluded every bear rec (2026-07-10: 4 pair_shorts at
        # 0.31-0.35 vs the 0.45 floor, 0 bear trades ever executed).
        bull_floor = overrides["min_score_threshold"]
        bear_floor = overrides["min_score_threshold_bear"]
        recs = [
            r for r in (
                self.db.query(Recommendation)
                .filter(Recommendation.date == today)
                .order_by(Recommendation.score.desc())
                .all()
            )
            if r.score >= (bear_floor if r.direction == "short" else bull_floor)
        ]

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

    def _already_submitted_today(self, rec: Recommendation) -> str | None:
        """Return existing order_id if this rec's (ticker, strategy) already submitted today."""
        today_start = datetime.combine(date.today(), time.min)
        existing = (
            self.db.query(TradingLog)
            .filter(
                TradingLog.ticker == rec.ticker,
                TradingLog.strategy == rec.strategy,
                TradingLog.action == "submit",
                TradingLog.created_at >= today_start,
            )
            .order_by(TradingLog.created_at.desc())
            .first()
        )
        return existing.order_id if existing else None

    def _persist_paper_trade(self, rec: Recommendation, order_id: str) -> None:
        """Create a PaperTrade row mirroring the submitted rec."""
        try:
            self.db.add(PaperTrade(
                ticker=rec.ticker,
                direction=rec.direction or "short",
                strategy=rec.strategy,
                status="open",
                entry_price=rec.entry_price or 0.0,
                stop_loss=rec.stop_loss,
                target_price=rec.target_price,
                position_size=rec.position_size,
                max_loss=rec.max_loss,
                contracts=rec.contracts,
                strike=rec.strike,
                option_type=rec.option_type,
                legs_json=rec.legs_json,
                expiry=rec.expiry,
                score=rec.score,
            ))
            self.db.commit()
        except Exception:
            logger.exception("PaperTrade persist failed for rec %s (order %s)", rec.id, order_id)
            self.db.rollback()

    def _with_live_leg_quotes(self, rec: Recommendation) -> str | None:
        """legs_json with bid/ask overwritten by live Alpaca option quotes.

        Returns the refreshed JSON string, or None when legs can't be parsed
        or turned into OCC symbols (caller keeps the original legs_json).
        Legs whose live quote is missing or one-sided get bid/ask set to None
        so the mapper's quote-dependent pricing treats them as quoteless
        instead of trusting stale rec-time values.
        """
        import json as _json

        from src.services.order_mapper import build_occ_symbol

        if not rec.legs_json or rec.expiry is None:
            return None
        try:
            legs = _json.loads(rec.legs_json)
        except (_json.JSONDecodeError, TypeError):
            return None
        if not isinstance(legs, list) or not legs:
            return None

        occ_symbols: list[str] = []
        try:
            for leg in legs:
                occ_symbols.append(
                    build_occ_symbol(
                        rec.ticker, rec.expiry, str(leg["option_type"]), float(leg["strike"])
                    )
                )
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"{rec.ticker} {rec.strategy}: cannot build OCC symbols for quote refresh: {e}")
            return None

        quotes = self.alpaca.get_option_quotes(occ_symbols)
        quoted = 0
        for leg, occ in zip(legs, occ_symbols):
            quote = quotes.get(occ)
            leg["bid"] = quote["bid"] if quote else None
            leg["ask"] = quote["ask"] if quote else None
            quoted += 1 if quote else 0
        if quoted < len(legs):
            logger.warning(
                f"{rec.ticker} {rec.strategy}: live quotes for {quoted}/{len(legs)} legs "
                f"({occ_symbols}) — quote-dependent pricing may drop this order"
            )
        return _json.dumps(legs)

    def _execute_single(
        self, rec: Recommendation, rails: TradingSafetyRails, market_open: bool,
    ) -> dict:
        """Execute a single recommendation through the pipeline."""
        # Hard kill-switch: ALPACA_TRADING_ENABLED is the env-level capability
        # gate — it must be explicitly true for ANY order submission, no matter
        # what the DB auto_execute_enabled runtime toggle says. Two-key design:
        # env = capability (deploy-time, protects against accidental live keys),
        # DB = intent (runtime on/off). Closing existing positions is NOT gated.
        if not self.settings.alpaca_trading_enabled:
            reason = "Blocked by ALPACA_TRADING_ENABLED=false (env kill-switch)"
            self._log(rec.ticker, "block", rec.strategy, reason=reason)
            return {
                "rec_id": rec.id, "ticker": rec.ticker,
                "status": "blocked", "reason": reason,
            }

        # Dedup: skip if (ticker, strategy) already submitted today
        dup_order = self._already_submitted_today(rec)
        if dup_order:
            reason = f"Already submitted today as {dup_order}"
            return {
                "rec_id": rec.id, "ticker": rec.ticker,
                "status": "duplicate", "order_id": dup_order, "reason": reason,
            }

        # Market-neutral pair (short pick + long hedge) is a two-order strategy
        # the single-order mapper can't express — dedicated path.
        if rec.strategy == "pair_short":
            return self._execute_pair(rec, rails, market_open)

        # Get buying power
        try:
            account = self.alpaca.get_account()
            buying_power = account["buying_power"]
        except Exception:
            buying_power = None

        # Spread legs: replace rec-time quotes with live ones. Recs are
        # generated 07:30 ET pre-market when yfinance chains carry bid=0/
        # ask=0, so legs_json quotes are dead by design; without live quotes
        # the mapper cannot price marketable MLEG limits (2026-07-15: both
        # credit spreads filled at giveaway cost-derived limits).
        legs_json = rec.legs_json
        if rec.strategy in ("spread", "bull_spread"):
            legs_json = self._with_live_leg_quotes(rec) or rec.legs_json

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
            expiry=rec.expiry,
            legs_json=legs_json,
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

        # For option contracts, Alpaca needs the OCC symbol (e.g.
        # AAPL250418P00150000), not the equity ticker. Mapper sets occ_symbol
        # on single-leg option params; fall back to ticker for stock orders.
        submit_symbol = order_params.occ_symbol or order_params.ticker

        # Submit to Alpaca
        try:
            if order_params.legs:
                # Multi-leg spread: per-leg OCC symbols already built by mapper.
                # mleg orders are submitted DAY-only (Alpaca rejects GTC on mleg).
                result = self.alpaca.submit_spread_order(
                    legs=order_params.legs,
                    qty=order_params.qty,
                    limit_price=order_params.limit_price,
                    time_in_force="day",
                )
            elif order_params.is_bracket:
                result = self.alpaca.submit_bracket_order(
                    ticker=submit_symbol,
                    qty=order_params.qty,
                    side=order_params.side,
                    limit_price=order_params.limit_price,
                    stop_loss_price=order_params.stop_loss_price,
                    take_profit_price=order_params.take_profit_price,
                    time_in_force=order_params.time_in_force,
                )
            else:
                result = self.alpaca.submit_order(
                    ticker=submit_symbol,
                    qty=order_params.qty,
                    side=order_params.side,
                    order_type=order_params.order_type,
                    limit_price=order_params.limit_price,
                    time_in_force=order_params.time_in_force,
                )

            order_id = result["order_id"]
            rails.log_submission(order_params, order_id)
            self._persist_paper_trade(rec, order_id)
            self._send_alert(rec.ticker, "submitted", order_id, rec.strategy)

            return {
                "rec_id": rec.id, "ticker": rec.ticker,
                "status": "submitted", "order_id": order_id,
            }

        except Exception as e:
            reason = f"Alpaca submission failed: {e}"
            self._log(rec.ticker, "error", rec.strategy, reason=reason)
            return {"rec_id": rec.id, "ticker": rec.ticker, "status": "error", "reason": reason}

    def _execute_pair(
        self, rec: Recommendation, rails: TradingSafetyRails, market_open: bool,
    ) -> dict:
        """Submit a pair_short rec: short the pick + long the hedge (legs_json).

        The short leg carries the risk and goes through the safety rails; the
        hedge leg is risk-reducing and submits alongside it. If the hedge
        submission fails after the short filled/accepted, the short is
        immediately unwound — a naked short is NOT an acceptable fallback
        (bear_monetization sweep: naked shorts lose).
        """
        import json as _json

        try:
            legs = _json.loads(rec.legs_json or "[]")
            short_leg = next(l for l in legs if l["leg"] == "short")
            hedge_leg = next(l for l in legs if l["leg"] == "hedge")
        except (ValueError, KeyError, StopIteration):
            reason = f"pair_short rec {rec.id} has malformed legs_json"
            self._log(rec.ticker, "skip", rec.strategy, reason=reason)
            return {"rec_id": rec.id, "ticker": rec.ticker, "status": "skipped", "reason": reason}

        from src.services.order_mapper import AlpacaOrderParams
        short_params = AlpacaOrderParams(
            ticker=short_leg["ticker"], qty=float(short_leg["qty"]), side="sell",
            order_type="market", strategy="pair_short",
        )
        allowed, rail_reason = rails.check_order(short_params, market_open=market_open)
        if not allowed:
            return {
                "rec_id": rec.id, "ticker": rec.ticker,
                "status": "blocked", "reason": rail_reason,
            }

        try:
            short_result = self.alpaca.submit_order(
                ticker=short_leg["ticker"], qty=float(short_leg["qty"]),
                side="sell", order_type="market", time_in_force="day",
            )
        except Exception as e:
            reason = f"pair short leg failed: {e}"
            self._log(rec.ticker, "error", rec.strategy, reason=reason)
            return {"rec_id": rec.id, "ticker": rec.ticker, "status": "error", "reason": reason}

        try:
            hedge_result = self.alpaca.submit_order(
                ticker=hedge_leg["ticker"], qty=float(hedge_leg["qty"]),
                side="buy", order_type="market", time_in_force="day",
            )
        except Exception as e:
            # Unwind the short — never leave a naked short standing.
            reason = f"pair hedge leg failed, unwinding short: {e}"
            logger.error(reason)
            try:
                self.alpaca.submit_order(
                    ticker=short_leg["ticker"], qty=float(short_leg["qty"]),
                    side="buy", order_type="market", time_in_force="day",
                )
            except Exception:
                logger.exception("pair unwind ALSO failed for %s — naked short at broker!", rec.ticker)
                self._send_alert(rec.ticker, "pair_unwind_failed", "", rec.strategy)
            self._log(rec.ticker, "error", rec.strategy, reason=reason)
            return {"rec_id": rec.id, "ticker": rec.ticker, "status": "error", "reason": reason}

        order_id = short_result["order_id"]
        hedge_order_id = hedge_result["order_id"]
        rails.log_submission(short_params, order_id)
        self._log(hedge_leg["ticker"], "submit", rec.strategy,
                  reason=f"hedge leg of {rec.ticker} pair ({hedge_order_id})")
        self._persist_paper_trade(rec, order_id)
        self._send_alert(rec.ticker, "submitted", order_id, rec.strategy)

        return {
            "rec_id": rec.id, "ticker": rec.ticker, "status": "submitted",
            "order_id": order_id, "hedge_order_id": hedge_order_id,
        }

    def monitor_fractional_exits(self) -> list[dict]:
        """Poll fractional long positions and close on stop/target breach.

        Alpaca brackets only apply to whole-share orders, so when the scheduler
        emits a fractional long via the `_map_long` fallback (qty<1, market
        order, no bracket), the broker has no automatic exit. This method
        serves as a polling bracket: scan open positions with non-integer qty,
        compare current_price against the originating recommendation's
        stop_loss/target_price, and call `close_position` on breach.

        Side note: Alpaca fractional shorts aren't supported, so fractional
        positions are effectively long-only — but the side check is included
        defensively in case a manual fractional short ever appears.

        Returns one dict per fractional position evaluated (closed, in-band,
        or error).
        """
        try:
            positions = self.alpaca.get_positions()
        except Exception:
            logger.exception("monitor_fractional_exits: get_positions failed")
            return []

        results: list[dict] = []
        for pos in positions:
            qty = pos.get("qty", 0)
            try:
                if float(qty).is_integer():
                    continue
            except (TypeError, ValueError):
                continue

            ticker = pos.get("ticker")
            current = pos.get("current_price") or 0
            side = (pos.get("side") or "long").lower()
            if not ticker or current <= 0:
                continue

            rec = (
                self.db.query(Recommendation)
                .filter_by(ticker=ticker)
                .order_by(Recommendation.date.desc(), Recommendation.id.desc())
                .first()
            )
            if rec is None:
                results.append({"ticker": ticker, "status": "skipped", "reason": "no_rec"})
                continue

            stop = rec.stop_loss
            target = rec.target_price
            breach: str | None = None

            if side == "long":
                if stop is not None and current <= stop:
                    breach = f"stop hit ({current:.2f} <= {stop:.2f})"
                elif target is not None and current >= target:
                    breach = f"target hit ({current:.2f} >= {target:.2f})"
            else:
                if stop is not None and current >= stop:
                    breach = f"stop hit ({current:.2f} >= {stop:.2f})"
                elif target is not None and current <= target:
                    breach = f"target hit ({current:.2f} <= {target:.2f})"

            if breach is None:
                results.append({"ticker": ticker, "status": "in_band", "current": current})
                continue

            try:
                close_result = self.alpaca.close_position(ticker)
                self._log(ticker, "fractional_exit", strategy=rec.strategy or "", reason=breach)
                self._send_alert(
                    ticker, "fractional_exit",
                    close_result.get("order_id", "") if isinstance(close_result, dict) else "",
                    rec.strategy or "",
                )
                results.append({
                    "ticker": ticker, "status": "closed", "reason": breach,
                    "order_id": close_result.get("order_id") if isinstance(close_result, dict) else None,
                })
            except Exception as e:
                logger.exception(f"monitor_fractional_exits: close {ticker} failed")
                results.append({"ticker": ticker, "status": "error", "reason": str(e)})

        return results

    def unwind_stock_trade(self, trade) -> dict:
        """Unwind the broker position for a paper-exit-closed stock trade.

        long/short: close the whole position (Alpaca handles side).
        pair_short: buy back the short-leg qty and sell the hedge-leg qty from
        legs_json — the hedge symbol (SPY) may be shared across pairs, so only
        this pair's quantity is sold, never close_position on the hedge.
        """
        import json as _json

        if trade.strategy in ("long", "short"):
            try:
                result = self.alpaca.close_position(trade.ticker)
                self._log(trade.ticker, "time_exit", trade.strategy,
                          reason=f"paper exit closed trade {trade.id}")
                return {"ticker": trade.ticker, "status": "closed",
                        "order_id": result.get("order_id") if isinstance(result, dict) else None}
            except Exception as e:
                logger.exception("unwind: close_position failed for %s", trade.ticker)
                return {"ticker": trade.ticker, "status": "error", "reason": str(e)}

        if trade.strategy == "pair_short":
            try:
                legs = _json.loads(trade.legs_json or "[]")
                short_leg = next(l for l in legs if l["leg"] == "short")
                hedge_leg = next(l for l in legs if l["leg"] == "hedge")
            except (ValueError, KeyError, StopIteration):
                return {"ticker": trade.ticker, "status": "error", "reason": "malformed legs_json"}
            results = {}
            try:
                results["short_close"] = self.alpaca.submit_order(
                    ticker=short_leg["ticker"], qty=float(short_leg["qty"]),
                    side="buy", order_type="market", time_in_force="day",
                )["order_id"]
            except Exception as e:
                logger.exception("unwind: pair short-leg buyback failed for %s", trade.ticker)
                results["short_close_error"] = str(e)
            try:
                results["hedge_close"] = self.alpaca.submit_order(
                    ticker=hedge_leg["ticker"], qty=float(hedge_leg["qty"]),
                    side="sell", order_type="market", time_in_force="day",
                )["order_id"]
            except Exception as e:
                logger.exception("unwind: pair hedge-leg sell failed for %s", trade.ticker)
                results["hedge_close_error"] = str(e)
            status = "closed" if "short_close" in results and "hedge_close" in results else "partial"
            self._log(trade.ticker, "time_exit", trade.strategy,
                      reason=f"pair unwind trade {trade.id}: {results}")
            return {"ticker": trade.ticker, "status": status, **results}

        return {"ticker": trade.ticker, "status": "skipped",
                "reason": f"no unwind path for strategy {trade.strategy}"}

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
