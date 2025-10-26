Frontend integration reference (shapes are aligned to existing FastAPI routers)

Base URL:
- Use REACT_APP_API_URL on the WebApplication (e.g., http://localhost:8000)

Auth:
- POST /auth/register { email, password, full_name?, role? } -> { access_token, refresh_token, token_type, expires_in }
- POST /auth/login { email, password } -> { access_token, refresh_token, token_type, expires_in }
- POST /auth/refresh { refresh_token } -> { access_token, refresh_token, token_type, expires_in }
- POST /auth/logout { refresh_token? } (Bearer token required) -> { status: "ok", revoked: <count|"single"> }
- GET  /auth/me (Bearer) -> { id, email, full_name, role, is_active, created_at, updated_at }

Users:
- GET  /users/me (Bearer) -> profile
- PATCH /users/me (Bearer) { full_name?, notification_preferences? } -> profile
- GET  /users (admin) -> { total, page, page_size, items: [...] }
- PATCH /users/{id} (admin) -> updated user snapshot

Classes:
- GET  /classes?start_from&start_to&trainer_id&page&page_size -> { total, page, page_size, items:[{..., booked_count}] }
- GET  /classes/{id} -> { ..., booked_count }
- POST /classes (trainer/admin) -> class details
- PATCH /classes/{id} (trainer/admin) -> class details
- DELETE /classes/{id} (trainer/admin) -> { status: "ok", deleted_id }

Bookings:
- POST /bookings (Bearer) { class_id, member_id? } -> { status:"ok", booking_id, class_id, user_id, booked_count, capacity }
- POST /bookings/cancel (Bearer) { class_id } -> { status:"ok", canceled_booking_id, class_id, booked_count, capacity }

Workouts:
- POST /workouts (trainer/admin) { member_id, date, type, duration_minutes?, notes? } -> workout
- GET  /workouts/me (Bearer) -> paginated workouts
- GET  /workouts/history (Bearer) -> paginated workouts (alias of /me)
- GET  /workouts/member/{user_id} (trainer/admin) -> paginated workouts for a user
- PATCH /workouts/{id} (Bearer) -> updated workout

Payments:
- POST /payments/create-checkout (Bearer) { amount, currency?, reference? } -> { provider, session_id?, client_secret?, payment_id, status, message }
  Note: Stub returns deterministic placeholders without Stripe keys.
- POST /payments/webhook (no auth) -> { status:"ok", processed, ... } returns 200 even without keys in test mode
- GET  /payments/history (Bearer) -> { total, items:[...] }
- GET  /payments/admin (admin) -> { total, items:[...] }

Notifications:
- POST /notifications/test (Bearer) { email?, phone?, subject?, message } -> { email_attempted, email_success, email_message, sms_attempted, sms_success, sms_message }
  Note: Works without SMTP/Twilio credentials and returns stubbed success/failure details.
- GET  /notifications/mine (Bearer) -> [logs] (DB-backed if table exists, else in-memory for current process)

Health:
- GET / -> { status:"ok", cors_allowed_origins:[...] }
- GET /docs/websocket -> WebSocket usage note (no WebSockets in this project)
