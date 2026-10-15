"""Authentication dependency for FastAPI.

Verifies Supabase JWTs and extracts the authenticated user ID.
The user ID is ALWAYS derived from the verified token's `sub` claim —
never from a request body, query parameter, or header value.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt

from app.config import Settings, get_settings


def _extract_bearer_token(request: Request) -> str:
    """Extract Bearer token from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHENTICATED", "message": "Missing or invalid Authorization header."},
        )
    return auth_header[7:]


def _verify_jwt(token: str, settings: Settings) -> str:
    """Verify Supabase JWT and return user_id (sub claim).

    Uses the configured JWT secret for HS256 verification.
    For production, JWKS-based RS256 verification is recommended.
    """
    if not settings.supabase_jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": "JWT verification not configured.",
            },
        )

    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHENTICATED", "message": "Invalid or expired token."},
        ) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHENTICATED", "message": "Token missing subject claim."},
        )

    # Validate it's a proper UUID
    try:
        uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHENTICATED", "message": "Invalid user ID in token."},
        ) from exc

    return user_id


async def get_current_user(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """FastAPI dependency: returns the authenticated user's UUID string.

    Usage:
        @router.get("/protected")
        async def protected(user_id: str = Depends(get_current_user)):
            ...
    """
    token = _extract_bearer_token(request)
    return _verify_jwt(token, settings)


# Type alias for cleaner route signatures
CurrentUser = Annotated[str, Depends(get_current_user)]
