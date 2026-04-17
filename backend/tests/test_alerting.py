"""Tests for alerting service thresholds and message formatting."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.services.alerting import AlertService, AlertType


class TestHighConvictionThreshold:
    def test_default_threshold_is_085(self):
        """High conviction alerts should default to 0.85, above the ensemble min_confidence of 0.75."""
        import inspect
        sig = inspect.signature(AlertService.check_high_conviction_alerts)
        default = sig.parameters["score_threshold"].default
        assert default == 0.85

    @pytest.mark.asyncio
    async def test_only_alerts_above_threshold(self):
        """Recommendations below threshold should not trigger alerts."""
        service = AlertService()
        service.send_alert = AsyncMock(return_value=True)

        mock_rec_high = MagicMock(
            ticker="AAPL", score=0.90, strategy="spread",
            directional_signal=0.85, volatility_signal=0.80,
            sentiment_signal=0.90, risk_type="defined",
            entry_price=150.0, position_size=500.0, max_loss=200.0,
            date=MagicMock(),
        )
        mock_rec_low = MagicMock(
            ticker="MSFT", score=0.60, strategy="short",
            directional_signal=0.60, volatility_signal=0.50,
            sentiment_signal=0.55, risk_type="undefined",
            entry_price=300.0, position_size=900.0, max_loss=45.0,
            date=MagicMock(),
        )

        from datetime import date
        with patch("src.services.alerting.SessionLocal") as mock_session:
            db = MagicMock()
            mock_session.return_value = db
            # Only return the high-score rec (DB filters by score >= 0.85)
            db.query.return_value.filter.return_value.all.return_value = [mock_rec_high]

            await service.check_high_conviction_alerts(score_threshold=0.85)

            # Should alert for AAPL (0.90 >= 0.85), not MSFT (filtered by DB query)
            assert service.send_alert.call_count == 1
            # send_alert called as positional args: (alert_type, ticker, message, details)
            assert service.send_alert.call_args[0][1] == "AAPL"

    @pytest.mark.asyncio
    async def test_alert_message_includes_risk_type(self):
        """Alert messages should include risk_type label."""
        service = AlertService()
        service.send_alert = AsyncMock(return_value=True)

        mock_rec = MagicMock(
            ticker="TSLA", score=0.92, strategy="spread",
            directional_signal=0.88, volatility_signal=0.82,
            sentiment_signal=0.91, risk_type="defined",
            entry_price=200.0, position_size=800.0, max_loss=300.0,
            date=MagicMock(),
        )

        with patch("src.services.alerting.SessionLocal") as mock_session:
            db = MagicMock()
            mock_session.return_value = db
            db.query.return_value.filter.return_value.all.return_value = [mock_rec]

            await service.check_high_conviction_alerts()

            message = service.send_alert.call_args[1].get("message") or service.send_alert.call_args[0][2]
            assert "[defined-risk]" in message

    @pytest.mark.asyncio
    async def test_alert_message_includes_model_agreement(self):
        """Alert messages should include how many models agreed."""
        service = AlertService()
        service.send_alert = AsyncMock(return_value=True)

        mock_rec = MagicMock(
            ticker="NVDA", score=0.88, strategy="options",
            directional_signal=0.90, volatility_signal=0.85,
            sentiment_signal=0.60,  # Below 0.75 — only 2/3 agree
            risk_type="defined",
            entry_price=500.0, position_size=900.0, max_loss=900.0,
            date=MagicMock(),
        )

        with patch("src.services.alerting.SessionLocal") as mock_session:
            db = MagicMock()
            mock_session.return_value = db
            db.query.return_value.filter.return_value.all.return_value = [mock_rec]

            await service.check_high_conviction_alerts()

            message = service.send_alert.call_args[0][2]
            assert "2/3 models agree" in message

    @pytest.mark.asyncio
    async def test_alert_details_include_signals(self):
        """Alert details should list individual signal scores."""
        service = AlertService()
        service.send_alert = AsyncMock(return_value=True)

        mock_rec = MagicMock(
            ticker="AMD", score=0.91, strategy="spread",
            directional_signal=0.88, volatility_signal=0.82,
            sentiment_signal=0.95, risk_type="defined",
            entry_price=120.0, position_size=600.0, max_loss=250.0,
            date=MagicMock(),
        )

        with patch("src.services.alerting.SessionLocal") as mock_session:
            db = MagicMock()
            mock_session.return_value = db
            db.query.return_value.filter.return_value.all.return_value = [mock_rec]

            await service.check_high_conviction_alerts()

            details = service.send_alert.call_args[1].get("details") or service.send_alert.call_args[0][3]
            assert "risk_type" in details
            assert details["risk_type"] == "defined"
            assert "signals" in details
            assert "directional=" in details["signals"]
