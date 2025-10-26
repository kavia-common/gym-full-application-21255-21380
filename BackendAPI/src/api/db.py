"""
Database module for SQLAlchemy engine and session management.

Creates the SQLAlchemy Engine using the DATABASE_URL from settings and exposes:
- Base: declarative base for ORM models
- get_db: FastAPI dependency that yields a session per request
- engine: configured SQLAlchemy engine
"""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import get_settings

settings = get_settings()

# Create SQLAlchemy engine
# The DATABASE_URL must be a SQLAlchemy-supported DSN.
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

# Create configured "Session" class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for declarative models
Base = declarative_base()


# PUBLIC_INTERFACE
def get_db() -> Generator:
    """Yield a database session and ensure it is closed after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
