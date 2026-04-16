"""JWT token creation and verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from src.config import get_settings


def create_access_token(username: str) -> str:
    settings = get_settings()
    payload = {
        "sub": username,
        "type": "access",
        "exp": datetime.now(timezone.utc)
        + timedelta(minutes=settings.jwt_access_expire_minutes),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def create_refresh_token(username: str) -> str:
    settings = get_settings()
    payload = {
        "sub": username,
        "type": "refresh",
        "exp": datetime.now(timezone.utc)
        + timedelta(days=settings.jwt_refresh_expire_days),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def verify_token(token: str, expected_type: str = "access") -> str | None:
    """Verify a JWT token and return the username, or None if invalid."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        if payload.get("type") != expected_type:
            return None
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
