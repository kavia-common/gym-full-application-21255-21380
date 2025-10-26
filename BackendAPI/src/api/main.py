from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .config import get_settings
from .db import engine
from . import models  # Ensure models are imported so metadata is available
from .routers import auth as auth_router
from .routers import users as users_router
from .routers import classes as classes_router
from .routers import bookings as bookings_router
from .routers import workouts as workouts_router
from .routers import payments as payments_router
from .routers import notifications as notifications_router
from .routers import reports as reports_router

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Backend API for the Gym Full Application. Provides authentication, scheduling, "
        "booking, workouts, and payments."
    ),
    openapi_tags=[
        {"name": "Health", "description": "Service health and diagnostics"},
        {"name": "Authentication", "description": "Register, login, token refresh, logout, and user profile"},
        {"name": "Users", "description": "User self-profile and admin user management"},
        {"name": "Classes", "description": "Create, manage, and list gym classes"},
        {"name": "Bookings", "description": "Class bookings with capacity enforcement"},
        {"name": "Workouts", "description": "Workout assignment and tracking for members"},
        {"name": "Payments", "description": "Payment creation, webhooks, and histories"},
        {"name": "Notifications", "description": "Email and SMS notifications (test and logs)"},
        {"name": "Reports", "description": "Admin-only reporting and CSV exports"},
    ],
)

# CORS configuration: allow http://localhost:3000 by default via settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    """
    App startup hook:
    - Validates database connectivity.
    - Optionally can create tables in development environments.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # Fail fast to surface DB connection issues
        raise RuntimeError(f"Database connectivity check failed: {exc}") from exc

    # For scaffolding purposes only: create tables if they don't exist (dev convenience)
    if settings.APP_ENV.lower() in ("development", "dev", "local"):
        models.Base.metadata.create_all(bind=engine)


# PUBLIC_INTERFACE
@app.get("/", tags=["Health"], summary="Health check", description="Returns service health status.")
def health_check():
    """Health endpoint used by probes and monitoring to verify service is up."""
    return {"status": "ok"}


# Include Routers
app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(classes_router.router)
app.include_router(bookings_router.router)
app.include_router(workouts_router.router)
app.include_router(payments_router.router)
app.include_router(notifications_router.router)
app.include_router(reports_router.router)
