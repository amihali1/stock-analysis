"""API routes for alerts and alert settings."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.db.models import Alert, AlertSetting
from src.db.session import SessionLocal

router = APIRouter()


class AlertSettingRequest(BaseModel):
    channel: str  # "discord" or "telegram"
    webhook_url: Optional[str] = None
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    enabled: bool = True
    score_threshold: float = 0.75
    alert_stop_loss: bool = True
    alert_target_hit: bool = True
    alert_high_conviction: bool = True


@router.get("/alerts")
def list_alerts(
    acknowledged: Optional[bool] = None,
    limit: int = Query(default=50, le=200),
):
    """List alert history."""
    db = SessionLocal()
    try:
        q = db.query(Alert).order_by(Alert.created_at.desc())
        if acknowledged is not None:
            q = q.filter(Alert.acknowledged == (1 if acknowledged else 0))
        alerts = q.limit(limit).all()
        return [
            {
                "id": a.id,
                "ticker": a.ticker,
                "alert_type": a.alert_type,
                "message": a.message,
                "acknowledged": bool(a.acknowledged),
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in alerts
        ]
    finally:
        db.close()


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int):
    """Mark an alert as acknowledged."""
    db = SessionLocal()
    try:
        alert = db.query(Alert).filter_by(id=alert_id).first()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        alert.acknowledged = 1
        db.commit()
        return {"status": "acknowledged"}
    finally:
        db.close()


@router.get("/alert-settings")
def get_alert_settings():
    """Get current alert settings."""
    db = SessionLocal()
    try:
        settings = db.query(AlertSetting).all()
        return [
            {
                "id": s.id,
                "channel": s.channel,
                "webhook_url": s.webhook_url,
                "enabled": bool(s.enabled),
                "score_threshold": s.score_threshold,
                "alert_stop_loss": bool(s.alert_stop_loss),
                "alert_target_hit": bool(s.alert_target_hit),
                "alert_high_conviction": bool(s.alert_high_conviction),
            }
            for s in settings
        ]
    finally:
        db.close()


@router.post("/alert-settings")
def create_alert_setting(req: AlertSettingRequest):
    """Create or update alert settings for a channel."""
    db = SessionLocal()
    try:
        # Upsert by channel
        existing = db.query(AlertSetting).filter_by(channel=req.channel).first()
        if existing:
            existing.webhook_url = req.webhook_url
            existing.bot_token = req.bot_token
            existing.chat_id = req.chat_id
            existing.enabled = 1 if req.enabled else 0
            existing.score_threshold = req.score_threshold
            existing.alert_stop_loss = 1 if req.alert_stop_loss else 0
            existing.alert_target_hit = 1 if req.alert_target_hit else 0
            existing.alert_high_conviction = 1 if req.alert_high_conviction else 0
        else:
            setting = AlertSetting(
                channel=req.channel,
                webhook_url=req.webhook_url,
                bot_token=req.bot_token,
                chat_id=req.chat_id,
                enabled=1 if req.enabled else 0,
                score_threshold=req.score_threshold,
                alert_stop_loss=1 if req.alert_stop_loss else 0,
                alert_target_hit=1 if req.alert_target_hit else 0,
                alert_high_conviction=1 if req.alert_high_conviction else 0,
            )
            db.add(setting)
        db.commit()
        return {"status": "saved", "channel": req.channel}
    finally:
        db.close()


@router.delete("/alert-settings/{setting_id}")
def delete_alert_setting(setting_id: int):
    """Delete an alert setting."""
    db = SessionLocal()
    try:
        setting = db.query(AlertSetting).filter_by(id=setting_id).first()
        if not setting:
            raise HTTPException(status_code=404, detail="Setting not found")
        db.delete(setting)
        db.commit()
        return {"status": "deleted"}
    finally:
        db.close()
