"""Authentication middleware for FastAPI."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.auth.jwt import verify_token

security = HTTPBearer(auto_error=False)

# Routes that don't require authentication
PUBLIC_PATHS = {
    "/api/health",
    "/api/auth/login",
    "/api/auth/refresh",
    "/docs",
    "/openapi.json",
    "/redoc",
}


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """Dependency that extracts and validates the JWT bearer token.

    Returns the username from the token payload.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username = verify_token(credentials.credentials, expected_type="access")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username
