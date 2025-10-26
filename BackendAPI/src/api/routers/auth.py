"""
Auth Router: registration, login, refresh, logout, and current user endpoints.

Routes:
- POST /auth/register
- POST /auth/login
- POST /auth/refresh
- POST /auth/logout
- GET  /auth/me
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..models import Users
from ..auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    persist_refresh_token,
    revoke_refresh_token,
    revoke_all_user_refresh_tokens,
    validate_refresh_token,
    get_current_user,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=8, description="User password (min 8 chars)")
    full_name: Optional[str] = Field(None, description="Full name")
    role: Optional[str] = Field(None, description="Optional role to assign (admin/trainer/member). Defaults to member.")


class TokenPairResponse(BaseModel):
    access_token: str = Field(..., description="Access JWT")
    refresh_token: str = Field(..., description="Refresh JWT")
    token_type: str = Field(default="bearer", description="Token type (bearer)")
    expires_in: int = Field(..., description="Access token TTL in seconds")


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="Email")
    password: str = Field(..., min_length=8, description="Password")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., description="Refresh token to rotate")


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = Field(
        None,
        description=(
            "Refresh token to revoke. If omitted, all user's tokens are revoked."
        ),
    )


def _user_visible(user: Users) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


# PUBLIC_INTERFACE
@router.post(
    "/register",
    summary="Register a new user",
    description="Creates a new user account with email/password and returns token pair.",
    response_model=TokenPairResponse,
)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenPairResponse:
    """Create a user account and return access/refresh tokens."""
    existing = db.execute(select(Users).where(Users.email == payload.email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    user = Users(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=(payload.role or "member"),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    access, access_exp = create_access_token(user)
    refresh, refresh_exp = create_refresh_token(user)
    persist_refresh_token(db, user, refresh, refresh_exp)

    return TokenPairResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=60 * 15,
    )


# PUBLIC_INTERFACE
@router.post(
    "/login",
    summary="Login",
    description=(
        "Authenticates a user and returns a new access/refresh token pair."
    ),
    response_model=TokenPairResponse,
)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenPairResponse:
    """Authenticate by email/password and return token pair on success."""
    user = db.execute(select(Users).where(Users.email == payload.email)).scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")

    access, access_exp = create_access_token(user)
    refresh, refresh_exp = create_refresh_token(user)
    persist_refresh_token(db, user, refresh, refresh_exp)

    return TokenPairResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=60 * 15,
    )


# PUBLIC_INTERFACE
@router.post(
    "/refresh",
    summary="Refresh access token",
    description=(
        "Rotates the refresh token (blacklisting the old one) and returns a new token pair."
    ),
    response_model=TokenPairResponse,
)
def refresh_tokens(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenPairResponse:
    """Rotate refresh token and return a new access and refresh token."""
    claims, row = validate_refresh_token(db, payload.refresh_token)

    # revoke the used refresh token (rotation)
    revoke_refresh_token(db, payload.refresh_token)

    # load the user
    user_id = int(claims.get("sub"))
    user = db.get(Users, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    access, _ = create_access_token(user)
    new_refresh, refresh_exp = create_refresh_token(user)
    persist_refresh_token(db, user, new_refresh, refresh_exp)

    return TokenPairResponse(
        access_token=access,
        refresh_token=new_refresh,
        token_type="bearer",
        expires_in=60 * 15,
    )


# PUBLIC_INTERFACE
@router.post(
    "/logout",
    summary="Logout",
    description=(
        "Revokes the provided refresh token or all tokens for the current user "
        "if none provided."
    ),
)
def logout(
    payload: LogoutRequest,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
) -> dict:
    """Logout by revoking refresh token(s)."""
    if payload.refresh_token:
        # attempt to revoke provided token
        revoke_refresh_token(db, payload.refresh_token)
        return {"status": "ok", "revoked": "single"}
    else:
        count = revoke_all_user_refresh_tokens(db, current_user.id)
        return {"status": "ok", "revoked": count}


# PUBLIC_INTERFACE
@router.get(
    "/me",
    summary="Current user",
    description="Returns the profile of the currently authenticated user.",
)
def me(current_user: Users = Depends(get_current_user)) -> dict:
    """Return current user details."""
    return _user_visible(current_user)
