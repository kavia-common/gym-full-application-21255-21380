"""
Shared dependencies for FastAPI routes.
"""

from typing import Generator

from .db import get_db as _get_db


# PUBLIC_INTERFACE
def db_session() -> Generator:
    """Convenience wrapper to expose DB session dependency."""
    yield from _get_db()
