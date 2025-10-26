"""
Scheduler for class reminder notifications.

This module attempts to use APScheduler's AsyncIOScheduler if available.
If APScheduler is not installed, it falls back to a lightweight background loop
that periodically scans for upcoming classes and dispatches reminders.

Reminder policy:
- For each Class.start_time, send reminders to booked members:
  - 24 hours before start
  - 1 hour before start
- Do not re-send the same reminder twice for the same (user_id, class_id, window).

User preferences:
- Since persistent notification preferences are not yet stored in DB, we respect the
  ephemeral NotificationPreferences provided via API only when we have them in session.
  For the scheduler (out-of-request), we apply defaults:
    email: True
    sms: False
  A future iteration can persist preferences and read them here.

Persistence:
- We avoid schema changes by using an in-memory process-local set to track sent reminders.
  This resets on process restart. A future iteration can persist reminder logs in DB.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Set, Tuple, List

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Classes, Bookings, Users
from .email_client import get_email_client
# from .sms_client import get_sms_client  # SMS disabled until Users.phone exists

# Detect APScheduler if present
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
    from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]
    APSCHEDULER_AVAILABLE = True
except Exception:  # pragma: no cover - optional dep
    APSCHEDULER_AVAILABLE = False


def _now() -> datetime:
    # Use timezone-aware UTC to compare with aware timestamps if any
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ReminderKey:
    """Unique key for a reminder to avoid duplicate sends within process lifetime."""
    user_id: int
    class_id: int
    window: str  # "24h" or "1h"


class ReminderDispatcher:
    """Encapsulates the logic to find upcoming reminders and send notifications."""

    def __init__(self) -> None:
        self._sent: Set[ReminderKey] = set()

    def _should_send(self, key: ReminderKey) -> bool:
        if key in self._sent:
            return False
        # mark as sent
        self._sent.add(key)
        return True

    def _human_dt(self, dt: datetime) -> str:
        try:
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            return dt.isoformat()

    def _query_upcoming(self, db: Session, window_minutes: int) -> List[Tuple[Classes, Users, Bookings]]:
        """
        Return tuples of (class, user, booking) for which a reminder should be sent
        window_minutes before class.start_time.
        """
        now = _now()
        # Consider a small tolerance window so we don't miss exact minute alignment.
        # We'll look ahead a minute range [window - tolerance, window + tolerance]
        tolerance = timedelta(minutes=2)
        target_start_from = now + timedelta(minutes=window_minutes) - tolerance
        target_start_to = now + timedelta(minutes=window_minutes) + tolerance

        # Only consider active 'booked' bookings and users who are active.
        # Join users to access email/phone; phone is not on the model.
        # If provided externally we cannot access it here.
        # We'll use email from Users; phone is unknown -> would require an SMS number field.
        # For SMS, we will skip sending unless Users has a 'phone' attribute (not defined).
        # Future: add phone field to Users and use it here.
        stmt = (
            select(Classes, Users, Bookings)
            .join(Bookings, Bookings.class_id == Classes.id)
            .join(Users, Users.id == Bookings.user_id)
            .where(
                and_(
                    Bookings.status == "booked",
                    Users.is_active == True,  # noqa: E712
                    Classes.start_time >= target_start_from,
                    Classes.start_time <= target_start_to,
                )
            )
        )
        rows = db.execute(stmt).all()
        return [(c, u, b) for (c, u, b) in rows]

    def _send_notifications(self, user: Users, gym_class: Classes, window: str) -> None:
        """
        Send email and/or SMS notification respecting default preferences.
        Email: default True
        SMS: default False (until preferences persistence is implemented and phone is available)
        """
        email_client = get_email_client()
        # sms_client = get_sms_client()  # Currently unused; SMS skipped until phone field exists

        # Build message
        start_human = self._human_dt(gym_class.start_time)
        subject = f"Reminder: {gym_class.name} starts in {window}"
        body = (
            f"Hello {user.full_name or user.email},\n\n"
            f"This is a friendly reminder that your class '{gym_class.name}' "
            f"starts at {start_human}.\n\n"
            "See you soon!\n"
            "- Gym Team"
        )

        # Email (default on)
        try:
            email_client.send_email(
                to_email=user.email,
                subject=subject,
                body=body,
            )
        except Exception:
            # Graceful: do not propagate exceptions from notification channels
            pass

        # SMS (default off): requires Users to have phone; not present in model.
        # If future adds Users.phone, uncomment:
        # if getattr(user, "phone", None):
        #     try:
        #         sms_client.send_sms(to_number=user.phone, body=f"{subject}\n{body}")
        #     except Exception:
        #         pass
        # For now, we skip SMS from scheduler.

    def run_scan_once(self) -> None:
        """Scan for upcoming classes and dispatch reminders for 24h and 1h windows."""
        with SessionLocal() as db:
            for minutes, window_tag in [(24 * 60, "24h"), (60, "1h")]:
                rows = self._query_upcoming(db, window_minutes=minutes)
                for cls, user, booking in rows:
                    key = ReminderKey(user_id=user.id, class_id=cls.id, window=window_tag)
                    if self._should_send(key):
                        self._send_notifications(user, cls, window=window_tag)


class SchedulerService:
    """
    Lifecycle-managed scheduler with two backends:
    - APScheduler AsyncIOScheduler (preferred)
    - Fallback: asyncio task loop that runs every minute
    """

    def __init__(self) -> None:
        self.dispatcher = ReminderDispatcher()
        self._scheduler: Optional["AsyncIOScheduler"] = None
        self._task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()

    async def start(self) -> None:
        if APSCHEDULER_AVAILABLE:
            self._start_apscheduler()
        else:
            self._start_fallback_loop()

    def _start_apscheduler(self) -> None:
        try:
            self._scheduler = AsyncIOScheduler()
            # Run every minute to catch 24h/1h windows with tolerance
            self._scheduler.add_job(self.dispatcher.run_scan_once, IntervalTrigger(minutes=1))
            self._scheduler.start()
        except Exception:
            # If anything goes wrong, fall back to background loop
            self._scheduler = None
            self._start_fallback_loop()

    def _start_fallback_loop(self) -> None:
        async def _loop() -> None:
            try:
                while not self._shutdown.is_set():
                    # run scan synchronously (it's CPU/IO-light)
                    try:
                        self.dispatcher.run_scan_once()
                    except Exception:
                        # swallow errors to keep loop healthy
                        pass
                    # sleep about a minute
                    try:
                        await asyncio.wait_for(self._shutdown.wait(), timeout=60.0)
                    except asyncio.TimeoutError:
                        continue
            finally:
                # exit
                pass

        self._task = asyncio.create_task(_loop(), name="reminder-scheduler-loop")

    async def stop(self) -> None:
        # Stop APScheduler
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

        # Stop fallback loop
        if self._task is not None:
            try:
                self._shutdown.set()
                await asyncio.wait([self._task], timeout=2.0)
            except Exception:
                pass
            self._task = None
            # Reset event for possible reuse
            self._shutdown = asyncio.Event()


_scheduler_instance: Optional[SchedulerService] = None


# PUBLIC_INTERFACE
def get_scheduler() -> SchedulerService:
    """Return a process-wide SchedulerService instance (singleton-style)."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = SchedulerService()
    return _scheduler_instance


@asynccontextmanager
async def scheduler_lifespan():
    """
    Async context manager to be used from FastAPI startup/shutdown hooks.
    Ensures the scheduler starts and stops with the app.
    """
    svc = get_scheduler()
    await svc.start()
    try:
        yield
    finally:
        await svc.stop()
