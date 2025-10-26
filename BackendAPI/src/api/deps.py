"""
Shared dependencies for FastAPI routes.
"""

from typing import Generator, List, Callable

from .db import get_db as _get_db
from .auth import get_current_user as _get_current_user, require_roles as _require_roles
from .models import Users


# PUBLIC_INTERFACE
def db_session() -> Generator:
    """Convenience wrapper to expose DB session dependency."""
    yield from _get_db()


# PUBLIC_INTERFACE
def get_current_user() -> Users:
    """Expose get_current_user dependency here for convenience re-exports."""
    return _get_current_user()  # type: ignore[misc]


# PUBLIC_INTERFACE
def require_roles(roles: List[str]) -> Callable[[Users], Users]:
    """Expose role guard dependency factory."""
    return _require_roles(roles)
