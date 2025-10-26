"""
Notifications Router: test email/SMS notifications and list my notification logs.

Endpoints:
- POST /notifications/test
- GET  /notifications/mine

Behavior:
- Uses SMTP and Twilio clients if credentials are present; otherwise returns stubbed
  no-op responses.
- Logs notification attempts into a notification_logs table if present; otherwise uses
  an in-memory stub for this process lifecycle.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Table, Column, Integer, String, DateTime, Boolean, MetaData, insert, select
from sqlalchemy.orm import Session

from ..deps import db_session as get_db, get_current_user
from ..models import Users
from ..email_client import get_email_client
from ..sms_client import get_sms_client

router = APIRouter(prefix="/notifications", tags=["Notifications"])

# ===== In-memory stub store if table is not available =====
_stub_logs: List[dict] = []

# Attempt to reference a notification_logs table dynamically if it exists.
# The ORM models module does not define it yet; we reflect table on demand.
metadata = MetaData()
notification_logs_table: Optional[Table] = None
try:
    # Define a compatible schema; reflect only if exists at runtime would require engine.
    # Here we define the table signature for insert/select if the DB has it.
    notification_logs_table = Table(
        "notification_logs",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("user_id", Integer, nullable=False),
        Column("channel", String(16), nullable=False),  # "email" or "sms"
        Column("recipient", String(255), nullable=False),
        Column("subject", String(255), nullable=True),
        Column("message", String(2048), nullable=False),
        Column("success", Boolean, nullable=False, default=False),
        Column("error", String(1024), nullable=True),
        Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
        extend_existing=True,
    )
except Exception:
    notification_logs_table = None


# ===== Schemas =====

class TestNotificationRequest(BaseModel):
    email: Optional[EmailStr] = Field(
        None,
        description=(
            "Override email to send test to; defaults to current user's email"
        ),
    )
    phone: Optional[str] = Field(
        None,
        description=(
            "Destination phone (E.164) for SMS test; if not provided, SMS is skipped"
        ),
    )
    subject: Optional[str] = Field("Gym App Test Notification", description="Email subject for the test email")
    message: str = Field("This is a test notification from Gym Full Application.", description="Body for email/SMS")


class NotificationLogItem(BaseModel):
    id: Optional[int] = Field(None, description="Log id (if persisted)")
    channel: str = Field(..., description="email or sms")
    recipient: str = Field(..., description="Recipient address/phone")
    subject: Optional[str] = Field(None, description="Subject for email")
    message: str = Field(..., description="Message content")
    success: bool = Field(..., description="Whether the send attempt succeeded")
    error: Optional[str] = Field(None, description="Error message if failed")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Timestamp of the log record")


class NotificationTestResponse(BaseModel):
    email_attempted: bool = Field(..., description="Whether an email send was attempted")
    email_success: Optional[bool] = Field(None, description="Success status for email")
    email_message: Optional[str] = Field(None, description="Details for email attempt")
    sms_attempted: bool = Field(..., description="Whether an SMS send was attempted")
    sms_success: Optional[bool] = Field(None, description="Success status for SMS")
    sms_message: Optional[str] = Field(None, description="Details for SMS attempt")


# ===== Helpers =====

def _insert_log_stub(
    user_id: int,
    channel: str,
    recipient: str,
    subject: Optional[str],
    message: str,
    success: bool,
    error: Optional[str],
) -> None:
    _stub_logs.append(
        {
            "id": len(_stub_logs) + 1,
            "user_id": user_id,
            "channel": channel,
            "recipient": recipient,
            "subject": subject,
            "message": message,
            "success": success,
            "error": error,
            "created_at": datetime.utcnow(),
        }
    )


def _insert_log_db(
    db: Session,
    user_id: int,
    channel: str,
    recipient: str,
    subject: Optional[str],
    message: str,
    success: bool,
    error: Optional[str],
) -> None:
    # If the notification_logs table is not available, fallback to stub memory.
    if notification_logs_table is None or not hasattr(db, "execute"):
        _insert_log_stub(user_id, channel, recipient, subject, message, success, error)
        return
    try:
        # Ensure metadata is bound and table exists; if not, fallback to stub.
        if not notification_logs_table.columns:
            _insert_log_stub(user_id, channel, recipient, subject, message, success, error)
            return
        db.execute(
            insert(notification_logs_table).values(
                user_id=user_id,
                channel=channel,
                recipient=recipient,
                subject=subject,
                message=message,
                success=success,
                error=error,
                created_at=datetime.utcnow(),
            )
        )
        db.commit()
    except Exception:
        # On any DB error, just use stub storage to avoid breaking the flow
        _insert_log_stub(user_id, channel, recipient, subject, message, success, error)


def _list_logs_db(db: Session, user_id: int) -> list[NotificationLogItem]:
    # Try to fetch from DB if table is present; else return stub logs
    items: list[NotificationLogItem] = []
    try:
        if notification_logs_table is not None:
            rows = db.execute(
                select(notification_logs_table)
                .where(notification_logs_table.c.user_id == user_id)
                .order_by(notification_logs_table.c.created_at.desc())
            ).all()
            for r in rows:
                rec = r._mapping  # typed mapping
                items.append(
                    NotificationLogItem(
                        id=rec.get("id"),
                        channel=rec.get("channel"),
                        recipient=rec.get("recipient"),
                        subject=rec.get("subject"),
                        message=rec.get("message"),
                        success=bool(rec.get("success")),
                        error=rec.get("error"),
                        created_at=rec.get("created_at") or datetime.utcnow(),
                    )
                )
            # If there are DB records, return them (even if empty)
            return items
    except Exception:
        # Fall through to stub
        pass

    # Stub fallback
    for log in sorted(
        [log_item for log_item in _stub_logs if int(log_item.get("user_id", 0)) == int(user_id)],
        key=lambda x: x.get("created_at"),
        reverse=True,
    ):
        items.append(
            NotificationLogItem(
                id=log.get("id"),
                channel=log.get("channel"),
                recipient=log.get("recipient"),
                subject=log.get("subject"),
                message=log.get("message"),
                success=bool(log.get("success")),
                error=log.get("error"),
                created_at=log.get("created_at") or datetime.utcnow(),
            )
        )
    return items


# ===== Routes =====

# PUBLIC_INTERFACE
@router.post(
    "/test",
    summary="Send test notification",
    description=(
        "Sends a test email to the current user's email (or provided override) and "
        "optionally a test SMS to the provided phone. Gracefully no-ops if "
        "SMTP/Twilio credentials are missing."
    ),
    response_model=NotificationTestResponse,
)
def send_test_notification(
    payload: TestNotificationRequest,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
) -> NotificationTestResponse:
    """
    Send a test email and/or SMS to validate notification delivery configuration.
    - Email always attempted to current user's email unless overridden; if SMTP is
      disabled, returns a no-op message.
    - SMS attempted only when phone is provided; if Twilio is disabled, returns a
      no-op message.
    """
    email_client = get_email_client()
    sms_client = get_sms_client()

    # Email
    email_attempted = True
    target_email = (payload.email or current_user.email)
    email_success, email_msg = email_client.send_email(
        to_email=target_email,
        subject=payload.subject or "Gym App Test Notification",
        body=payload.message,
    )
    _insert_log_db(
        db=db,
        user_id=current_user.id,
        channel="email",
        recipient=target_email,
        subject=payload.subject or "Gym App Test Notification",
        message=payload.message,
        success=email_success,
        error=None if email_success else email_msg,
    )

    # SMS (only if provided)
    sms_attempted = bool(payload.phone)
    sms_success: Optional[bool] = None
    sms_msg: Optional[str] = None
    if payload.phone:
        sms_success, sms_msg = sms_client.send_sms(to_number=payload.phone, body=payload.message)
        _insert_log_db(
            db=db,
            user_id=current_user.id,
            channel="sms",
            recipient=payload.phone,
            subject=None,
            message=payload.message,
            success=bool(sms_success),
            error=None if sms_success else sms_msg,
        )

    return NotificationTestResponse(
        email_attempted=email_attempted,
        email_success=email_success,
        email_message=email_msg,
        sms_attempted=sms_attempted,
        sms_success=sms_success,
        sms_message=sms_msg,
    )


# PUBLIC_INTERFACE
@router.get(
    "/mine",
    summary="List my notification logs",
    description=(
        "Returns the current user's notification logs. If the notification_logs table "
        "is not present, returns logs from an in-memory stub for this application "
        "process."
    ),
    response_model=list[NotificationLogItem],
)
def list_my_notifications(
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
) -> list[NotificationLogItem]:
    """
    Returns a list of notification attempts for the current user, newest first.
    Uses DB if available, otherwise in-memory stub.
    """
    return _list_logs_db(db, user_id=current_user.id)
