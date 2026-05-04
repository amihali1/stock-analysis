"""Alerting service: push notifications for stop-loss, target, and high-conviction signals."""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum

import httpx

from src.db.models import Alert, PaperTrade
from src.db.session import SessionLocal

logger = logging.getLogger(__name__)


class AlertType(str, Enum):
    STOP_LOSS = "stop_loss"
    TARGET_HIT = "target_hit"
    HIGH_CONVICTION = "high_conviction"
    POSITION_CLOSED = "position_closed"


class AlertChannel(str, Enum):
    DISCORD = "discord"
    TELEGRAM = "telegram"


class AlertService:
    """Send alerts via Discord or Telegram webhooks."""

    def __init__(self, discord_webhook: str = "", telegram_bot_token: str = "", telegram_chat_id: str = ""):
        self.discord_webhook = discord_webhook
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id

    async def send_alert(
        self,
        alert_type: AlertType,
        ticker: str,
        message: str,
        details: dict | None = None,
    ) -> bool:
        """Send an alert and store it in the database.

        Returns True if at least one channel succeeded.
        """
        # Store in DB
        db = SessionLocal()
        try:
            alert = Alert(
                ticker=ticker,
                alert_type=alert_type.value,
                message=message,
                details_json=str(details) if details else None,
                created_at=datetime.utcnow(),
            )
            db.add(alert)
            db.commit()
        finally:
            db.close()

        sent = False

        if self.discord_webhook:
            try:
                await self._send_discord(ticker, alert_type, message, details)
                sent = True
            except Exception as e:
                logger.error(f"Discord alert failed: {e}")

        if self.telegram_bot_token and self.telegram_chat_id:
            try:
                await self._send_telegram(ticker, alert_type, message, details)
                sent = True
            except Exception as e:
                logger.error(f"Telegram alert failed: {e}")

        if not sent:
            logger.warning(f"Alert stored but no channels configured: [{alert_type.value}] {ticker}: {message}")

        return sent

    async def _send_discord(self, ticker: str, alert_type: AlertType, message: str, details: dict | None):
        """Send alert to Discord webhook."""
        color_map = {
            AlertType.STOP_LOSS: 0xFF0000,     # Red
            AlertType.TARGET_HIT: 0x00FF00,     # Green
            AlertType.HIGH_CONVICTION: 0xFFAA00, # Orange
            AlertType.POSITION_CLOSED: 0x0099FF, # Blue
        }

        embed = {
            "title": f"{alert_type.value.replace('_', ' ').title()}: {ticker}",
            "description": message,
            "color": color_map.get(alert_type, 0x808080),
            "timestamp": datetime.utcnow().isoformat(),
        }

        if details:
            embed["fields"] = [
                {"name": k, "value": str(v), "inline": True}
                for k, v in details.items()
            ]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.discord_webhook,
                json={"embeds": [embed]},
                timeout=10,
            )
            resp.raise_for_status()

    async def _send_telegram(self, ticker: str, alert_type: AlertType, message: str, details: dict | None):
        """Send alert to Telegram bot."""
        text = f"*{alert_type.value.replace('_', ' ').title()}*: {ticker}\n{message}"
        if details:
            for k, v in details.items():
                text += f"\n{k}: {v}"

        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": self.telegram_chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            resp.raise_for_status()

    async def send_test(self, setting) -> bool:
        """Send a test alert using a specific AlertSetting record."""
        service = AlertService(
            discord_webhook=setting.webhook_url or "",
            telegram_bot_token=setting.bot_token or "",
            telegram_chat_id=setting.chat_id or "",
        )
        return await service.send_alert(
            AlertType.HIGH_CONVICTION,
            "TEST",
            "This is a test alert from Stock Analysis Platform.",
            {"source": "test_button"},
        )

    async def check_paper_trade_alerts(self):
        """Check open paper trades for stop-loss or target hits against latest prices."""
        from src.db.models import PriceHistory

        db = SessionLocal()
        try:
            open_trades = db.query(PaperTrade).filter_by(status="open").all()

            for trade in open_trades:
                latest_price = (
                    db.query(PriceHistory)
                    .filter_by(ticker=trade.ticker)
                    .order_by(PriceHistory.date.desc())
                    .first()
                )
                if not latest_price or not latest_price.close:
                    continue

                current = latest_price.close

                # Short stop-loss: price went above stop
                if trade.strategy == "short" and trade.stop_loss and current >= trade.stop_loss:
                    await self.send_alert(
                        AlertType.STOP_LOSS, trade.ticker,
                        f"Short stop-loss hit. Current: ${current:.2f}, Stop: ${trade.stop_loss:.2f}",
                        {"entry": trade.entry_price, "position_size": trade.position_size},
                    )

                # Short target: price went below target
                elif trade.strategy == "short" and trade.target_price and current <= trade.target_price:
                    await self.send_alert(
                        AlertType.TARGET_HIT, trade.ticker,
                        f"Short target hit. Current: ${current:.2f}, Target: ${trade.target_price:.2f}",
                        {"entry": trade.entry_price, "position_size": trade.position_size},
                    )

        finally:
            db.close()

    async def check_high_conviction_alerts(self, score_threshold: float = 0.85):
        """Check for new high-conviction recommendations.

        Since the ensemble already filters at min_directional_lift +
        min_sentiment_confidence (P10-004), this threshold is intentionally
        higher to surface only standout signals beyond the gate.
        Only defined-risk trades or very strong undefined-risk signals alert.
        """
        from src.db.models import Recommendation
        from datetime import date

        db = SessionLocal()
        try:
            today = date.today()
            high_recs = (
                db.query(Recommendation)
                .filter(
                    Recommendation.date == today,
                    Recommendation.score >= score_threshold,
                )
                .all()
            )

            for rec in high_recs:
                # Build signal agreement summary
                signals = []
                if rec.directional_signal and rec.directional_signal >= 0.75:
                    signals.append(f"directional={rec.directional_signal:.2f}")
                if rec.volatility_signal and rec.volatility_signal >= 0.75:
                    signals.append(f"volatility={rec.volatility_signal:.2f}")
                if rec.sentiment_signal and rec.sentiment_signal >= 0.75:
                    signals.append(f"sentiment={rec.sentiment_signal:.2f}")

                agreement = f"{len(signals)}/3 models agree"
                risk_label = f"[{rec.risk_type or 'undefined'}-risk]"

                await self.send_alert(
                    AlertType.HIGH_CONVICTION, rec.ticker,
                    f"{risk_label} High conviction {rec.strategy} signal "
                    f"(score: {rec.score:.2f}, {agreement})",
                    {
                        "score": rec.score,
                        "strategy": rec.strategy,
                        "risk_type": rec.risk_type or "undefined",
                        "signals": ", ".join(signals) if signals else "below threshold",
                        "entry_price": rec.entry_price,
                        "position_size": rec.position_size,
                        "max_loss": rec.max_loss,
                    },
                )
        finally:
            db.close()
