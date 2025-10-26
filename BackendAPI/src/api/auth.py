"""
Authentication utilities: password hashing, JWT access/refresh creation and verification,
refresh token persistence and rotation, and FastAPI dependencies for current user and role guards.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Tuple, Callable, List

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt  # type: ignore[import-untyped]
from passlib.context import CryptContext  # type: ignore[import-untyped]
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select, update

from .config import get_settings
from .db import get_db
from .models import Users, RefreshTokens

# Password hashing context using bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Bearer auth scheme
bearer_scheme = HTTPBearer(auto_error=True)

settings = get_settings()


class JWTSettings(BaseModel):
    """Configuration for JWT token generation and validation loaded from env."""
    SECRET_KEY: str = Field(..., description="Secret key used to sign JWTs")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=15, description="TTL for access tokens in minutes")
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, description="TTL for refresh tokens in days")
    ISSUER: str = Field(default="gym-backend", description="JWT issuer")
    AUDIENCE: str = Field(default="gym-web", description="JWT audience")

    @classmethod
    def from_env(cls) -> "JWTSettings":
        """
        Load from AppSettings; missing envs will raise at app startup.
        Expected env vars:
        - SECRET_KEY
        - ACCESS_TOKEN_EXPIRE_MINUTES (optional, default 15)
        - REFRESH_TOKEN_EXPIRE_DAYS (optional, default 7)
        - JWT_ISSUER (optional)
        - JWT_AUDIENCE (optional)
        """
        # AppSettings doesn't define these explicitly; use getattr with defaults by
        # reading from environment via BaseSettings behavior. We'll piggy-back on
        # settings.model_extra or environment directly; to keep simple, use
        # os.getenv fallbacks.
        import os

        secret = os.getenv("SECRET_KEY")
        if not secret:
            raise RuntimeError("SECRET_KEY env variable must be set for JWT signing")

        access_minutes = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
        refresh_days = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
        issuer = os.getenv("JWT_ISSUER", "gym-backend")
        audience = os.getenv("JWT_AUDIENCE", "gym-web")

        return cls(
            SECRET_KEY=secret,
            ACCESS_TOKEN_EXPIRE_MINUTES=access_minutes,
            REFRESH_TOKEN_EXPIRE_DAYS=refresh_days,
            ISSUER=issuer,
            AUDIENCE=audience,
        )


jwt_settings = JWTSettings.from_env()

ALGORITHM = "HS256"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# PUBLIC_INTERFACE
def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return pwd_context.hash(password)


# PUBLIC_INTERFACE
def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a hashed password."""
    return pwd_context.verify(plain_password, password_hash)


def _common_claims(user: Users) -> Dict[str, Any]:
    return {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "iss": jwt_settings.ISSUER,
        "aud": jwt_settings.AUDIENCE,
        "iat": int(_now_utc().timestamp()),
    }


# PUBLIC_INTERFACE
def create_access_token(user: Users) -> Tuple[str, datetime]:
    """Create a signed access JWT for the given user and return (token, expires_at)."""
    expires_at = _now_utc() + timedelta(minutes=jwt_settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = _common_claims(user)
    payload.update({"type": "access", "exp": int(expires_at.timestamp())})
    token = jwt.encode(payload, jwt_settings.SECRET_KEY, algorithm=ALGORITHM)
    return token, expires_at


# PUBLIC_INTERFACE
def create_refresh_token(user: Users) -> Tuple[str, datetime]:
    """Create a signed refresh JWT for the given user and return (token, expires_at)."""
    expires_at = _now_utc() + timedelta(days=jwt_settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = _common_claims(user)
    payload.update({"type": "refresh", "exp": int(expires_at.timestamp())})
    token = jwt.encode(payload, jwt_settings.SECRET_KEY, algorithm=ALGORITHM)
    return token, expires_at


# PUBLIC_INTERFACE
def persist_refresh_token(db: Session, user: Users, token: str, expires_at: datetime) -> RefreshTokens:
    """
    Persist a refresh token for the user. Returns the created RefreshTokens row.
    """
    row = RefreshTokens(user_id=user.id, token=token, expires_at=expires_at, revoked=False)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# PUBLIC_INTERFACE
def revoke_refresh_token(db: Session, token: str) -> None:
    """Mark a specific refresh token as revoked if it exists."""
    db.execute(
        update(RefreshTokens)
        .where(RefreshTokens.token == token)
        .values(revoked=True)
    )
    db.commit()


# PUBLIC_INTERFACE
def revoke_all_user_refresh_tokens(db: Session, user_id: int) -> int:
    """Revoke all refresh tokens for a user. Returns number of tokens affected."""
    result = db.execute(
        update(RefreshTokens)
        .where(RefreshTokens.user_id == user_id, RefreshTokens.revoked == False)  # noqa: E712
        .values(revoked=True)
    )
    db.commit()
    return result.rowcount or 0


def _decode_token(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            jwt_settings.SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=jwt_settings.AUDIENCE,
            options={"verify_aud": True},
        )
        return payload
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc


# PUBLIC_INTERFACE
def validate_access_token(token: str) -> Dict[str, Any]:
    """Validate access token and return payload."""
    payload = _decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    return payload


# PUBLIC_INTERFACE
def validate_refresh_token(db: Session, token: str) -> Tuple[Dict[str, Any], RefreshTokens]:
    """
    Validate refresh token, ensure it exists in DB, not revoked, and not expired.
    Returns (payload, db_row).
    """
    payload = _decode_token(token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    stmt = select(RefreshTokens).where(RefreshTokens.token == token)
    row = db.execute(stmt).scalar_one_or_none()
    if not row or row.revoked:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or revoked refresh token")

    # extra safety: ensure not expired by DB clock
    if row.expires_at < _now_utc():
        # mark revoked to prevent reuse
        row.revoked = True
        db.add(row)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")

    return payload, row


# Dependencies

# PUBLIC_INTERFACE
def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Users:
    """
    Dependency that validates the Bearer access token and returns the current User.
    """
    token = creds.credentials
    payload = validate_access_token(token)
    user_id = int(payload.get("sub", "0"))
    user = db.get(Users, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or not found")
    return user


# PUBLIC_INTERFACE
def require_roles(roles: List[str]) -> Callable[[Users], Users]:
    """
    Role guard factory. Use as dependency in routes to restrict by role(s).

    Example:
        @router.get("/admin", dependencies=[Depends(require_roles(["admin"]))])
    """
    def _guard(user: Users = Depends(get_current_user)) -> Users:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return _guard
