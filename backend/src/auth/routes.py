"""Authentication API routes."""

from __future__ import annotations

from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from src.auth.jwt import create_access_token, create_refresh_token, verify_token
from src.db.models import User
from src.db.session import SessionLocal

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def ensure_default_admin():
    """Create default admin user if no users exist."""
    from src.config import get_settings

    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            return
        settings = get_settings()
        admin = User(
            username=settings.default_admin_username,
            password_hash=hash_password(settings.default_admin_password),
        )
        db.add(admin)
        db.commit()
    finally:
        db.close()


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=body.username).first()
        if not user or not check_password(body.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account disabled",
            )

        user.last_login = datetime.now(timezone.utc)
        db.commit()

        return TokenResponse(
            access_token=create_access_token(user.username),
            refresh_token=create_refresh_token(user.username),
        )
    finally:
        db.close()


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest):
    username = verify_token(body.refresh_token, expected_type="refresh")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=username).first()
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or disabled",
            )
    finally:
        db.close()

    return TokenResponse(
        access_token=create_access_token(username),
        refresh_token=create_refresh_token(username),
    )


@router.post("/auth/logout")
def logout():
    """Logout endpoint. Client should discard tokens."""
    return {"detail": "Logged out successfully"}
