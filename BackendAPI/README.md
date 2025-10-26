# BackendAPI

FastAPI backend for the Gym Full Application.

## Quick start

1. Create and populate a .env file (see .env.example)

2. Install dependencies:
   pip install -r requirements.txt

3. Run:
   uvicorn src.api.main:app --reload

The app exposes a health endpoint at GET / and enables CORS for http://localhost:3000 by default.

## Configuration

Config uses Pydantic BaseSettings (see src/api/config.py). Required:
- DATABASE_URL: Postgres DSN (e.g., postgresql+psycopg://user:pass@localhost:5000/db)
- CORS_ALLOW_ORIGINS: defaults to http://localhost:3000

## Database

- SQLAlchemy engine is created from DATABASE_URL (src/api/db.py).
- Models scaffolded in src/api/models.py.
- On startup in development mode, tables are created automatically for convenience.
