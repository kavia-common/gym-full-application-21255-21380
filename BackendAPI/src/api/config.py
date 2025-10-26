"""
Configuration module for BackendAPI using Pydantic BaseSettings.

This module defines the AppSettings class which loads configuration from
environment variables, enabling flexible configuration across environments.

Environment variables required:
- DATABASE_URL: SQLAlchemy-compatible Postgres URL
- CORS_ALLOW_ORIGINS: Comma-separated origins for CORS (default: http://localhost:3000)
- APP_ENV: Application environment string (development, staging, production)
- APP_NAME: Name of the FastAPI application
- APP_VERSION: Version string for the API
"""

from functools import lru_cache
from typing import List

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Application settings loaded from environment variables with defaults."""

    APP_NAME: str = Field(default="Gym Full Application Backend API", description="Application name")
    APP_VERSION: str = Field(default="0.1.0", description="Application version")
    APP_ENV: str = Field(default="development", description="Application environment")

    # Database
    DATABASE_URL: str = Field(
        ...,
        description=(
            "SQLAlchemy DSN for Postgres, e.g., "
            "postgresql+psycopg://user:pass@host:port/db"
        ),
    )

    # CORS
    CORS_ALLOW_ORIGINS: str = Field(
        default="http://localhost:3000",
        description="Comma-separated list of allowed CORS origins",
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    # PUBLIC_INTERFACE
    def allowed_origins_list(self) -> List[str]:
        """Return the list of allowed CORS origins parsed from CORS_ALLOW_ORIGINS."""
        return [o.strip() for o in self.CORS_ALLOW_ORIGINS.split(",") if o.strip()]


# PUBLIC_INTERFACE
@lru_cache
def get_settings() -> AppSettings:
    """Return cached AppSettings instance; raises ValidationError if invalid."""
    try:
        return AppSettings()  # type: ignore[call-arg]
    except ValidationError as exc:
        # Re-raise so the app fails fast on misconfiguration
        raise exc
