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

Optional (Notifications):
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM  (email via SMTP)
- TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM    (SMS via Twilio)

Notes:
- If notification credentials are not set, the API gracefully no-ops and returns stubbed messages.
- Endpoints:
  - POST /notifications/test  (send test email to current user's email; optional SMS to provided phone)
  - GET  /notifications/mine  (list notification attempts; uses DB table notification_logs if present, else stub memory)

## Database

- SQLAlchemy engine is created from DATABASE_URL (src/api/db.py).
- Models scaffolded in src/api/models.py.
- On startup in development mode, tables are created automatically for convenience.
