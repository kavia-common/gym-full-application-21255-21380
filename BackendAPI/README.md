# BackendAPI

FastAPI backend for the Gym Full Application.

## Quick start

1. Create and populate a .env file (see .env.example for all required/optional variables)
2. Install dependencies:
   pip install -r requirements.txt
3. Run:
   uvicorn src.api.main:app --reload

Health: GET / returns {"status":"ok"} and lists allowed CORS origins.

CORS: By default allows http://localhost:3000. Set CORS_ALLOW_ORIGINS if your frontend runs elsewhere.

## Configuration

Uses Pydantic BaseSettings (see src/api/config.py) and environment variables (.env). Required for smoke tests:
- DATABASE_URL: Postgres DSN (e.g., postgresql+psycopg://user:pass@localhost:5432/db)
- SECRET_KEY: required for JWT signing
- CORS_ALLOW_ORIGINS: defaults to http://localhost:3000; include your frontend origin if different

Optional (Payments stub works without these):
- STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET

Optional (Notifications):
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM  (email via SMTP)
- TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM    (SMS via Twilio)

Notes:
- If notification credentials are not set, the API gracefully no-ops and returns stubbed messages.
- Endpoints:
  - POST /notifications/test  (succeeds with no-op when credentials absent)
  - GET  /notifications/mine  (lists attempts; uses DB table notification_logs if present, else in-memory stub)
- Payments:
  - POST /payments/create-checkout returns deterministic stub session_id/client_secret when Stripe keys are absent
  - POST /payments/webhook accepts unsigned payloads in test mode (no webhook secret)

## Database

- SQLAlchemy engine is created from DATABASE_URL (src/api/db.py).
- Models scaffolded in src/api/models.py.
- On startup in development mode, tables are created automatically for convenience.
