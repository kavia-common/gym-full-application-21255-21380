"""
Reports Router: admin-only overview metrics and CSV export.

Endpoints:
- GET /reports/overview          (admin) Summary metrics across users, classes, bookings, workouts, payments
- GET /reports/export.csv        (admin) CSV export for a selected dataset type

Notes:
- Uses role guard to restrict to admin.
- CSV is generated on the fly using Python's csv module and returned as text/csv.
"""

from datetime import date, datetime
from io import StringIO
import csv
from typing import Optional, List, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from ..deps import db_session as get_db, get_current_user, require_roles
from ..models import Users, Classes, Bookings, Workouts, Payments, PaymentStatusEnum

router = APIRouter(
    prefix="/reports",
    tags=["Reports"],
    dependencies=[Depends(require_roles(["admin"]))],  # All endpoints here are admin-only
)


# ======== Schemas ========

class OverviewMetrics(BaseModel):
    total_users: int = Field(..., description="Total number of users")
    active_users: int = Field(..., description="Number of users with is_active=true")
    total_classes: int = Field(..., description="Total classes")
    upcoming_classes: int = Field(..., description="Number of classes with start_time in the future")
    total_bookings: int = Field(..., description="Total bookings (all statuses)")
    active_bookings: int = Field(..., description="Bookings with status='booked'")
    total_workouts: int = Field(..., description="Total workouts")
    total_payments: int = Field(..., description="Total payments")
    payments_success: int = Field(..., description="Count of successful payments")
    payments_failed: int = Field(..., description="Count of failed payments")
    payments_refunded: int = Field(..., description="Count of refunded payments")
    payments_amount_success: float = Field(..., description="Sum of amounts for successful payments")


# ======== Helpers ========

def _now_utc() -> datetime:
    return datetime.utcnow()


def _count(db: Session, stmt) -> int:
    return int(db.execute(stmt).scalar_one() or 0)


def _sum(db: Session, stmt) -> float:
    val = db.execute(stmt).scalar_one()
    try:
        return float(val or 0)
    except Exception:
        return 0.0


def _overview(db: Session) -> OverviewMetrics:
    # Users
    total_users = _count(db, select(func.count(Users.id)))
    active_users = _count(db, select(func.count(Users.id)).where(Users.is_active == True))  # noqa: E712

    # Classes
    total_classes = _count(db, select(func.count(Classes.id)))
    upcoming_classes = _count(db, select(func.count(Classes.id)).where(Classes.start_time > _now_utc()))

    # Bookings
    total_bookings = _count(db, select(func.count(Bookings.id)))
    active_bookings = _count(db, select(func.count(Bookings.id)).where(Bookings.status == "booked"))

    # Workouts
    total_workouts = _count(db, select(func.count(Workouts.id)))

    # Payments: counts by status and sum of successful
    total_payments = _count(db, select(func.count(Payments.id)))
    payments_success = _count(
        db,
        select(func.count(Payments.id)).where(
            Payments.status == PaymentStatusEnum.SUCCESS.value
        ),
    )
    payments_failed = _count(
        db,
        select(func.count(Payments.id)).where(
            Payments.status == PaymentStatusEnum.FAILED.value
        ),
    )
    payments_refunded = _count(
        db,
        select(func.count(Payments.id)).where(
            Payments.status == PaymentStatusEnum.REFUNDED.value
        ),
    )
    payments_amount_success = _sum(
        db,
        select(func.coalesce(func.sum(Payments.amount), 0)).where(Payments.status == PaymentStatusEnum.SUCCESS.value),
    )

    return OverviewMetrics(
        total_users=total_users,
        active_users=active_users,
        total_classes=total_classes,
        upcoming_classes=upcoming_classes,
        total_bookings=total_bookings,
        active_bookings=active_bookings,
        total_workouts=total_workouts,
        total_payments=total_payments,
        payments_success=payments_success,
        payments_failed=payments_failed,
        payments_refunded=payments_refunded,
        payments_amount_success=payments_amount_success,
    )


def _csv_from_rows(headers: List[str], rows: List[Tuple]) -> str:
    """
    Build CSV string from headers and rows.
    """
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        # Row may be ORM objects; normalize to tuple
        if not isinstance(row, (tuple, list)):
            # attempt mapping for ORM object
            try:
                row = tuple(getattr(row, col) for col in headers)
            except Exception:
                row = (str(row),)
        writer.writerow(row)
    return buf.getvalue()


# ======== Routes ========

# PUBLIC_INTERFACE
@router.get(
    "/overview",
    summary="Admin overview metrics",
    description="Returns high-level metrics across users, classes, bookings, workouts, and payments.",
    response_model=OverviewMetrics,
)
def reports_overview(
    db: Session = Depends(get_db),
    admin_user: Users = Depends(get_current_user),
) -> OverviewMetrics:
    """
    Returns admin overview metrics. Requires admin role via router-level dependency.
    """
    if admin_user.role != "admin":
        # Redundant due to router dependencies, but explicit check ensures clarity
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return _overview(db)


# PUBLIC_INTERFACE
@router.get(
    "/export.csv",
    summary="Export CSV data",
    description=(
        "Exports CSV for the requested dataset. Supported types: users, classes, bookings, workouts, payments. "
        "Optional date filters apply to classes (start_time), workouts (date), and payments (created_at)."
    ),
    response_class=PlainTextResponse,
)
def export_csv(
    db: Session = Depends(get_db),
    admin_user: Users = Depends(get_current_user),
    type: str = Query(..., description="Dataset to export: users|classes|bookings|workouts|payments"),
    # Optional filtering
    from_date: Optional[date] = Query(None, description="Filter by date >= (applies to classes/workouts/payments)"),
    to_date: Optional[date] = Query(None, description="Filter by date <= (applies to classes/workouts/payments)"),
    status_filter: Optional[str] = Query(None, description="Payments only: filter by status"),
) -> PlainTextResponse:
    """
    Export selected dataset as CSV. Only admins are allowed.

    Columns:
    - users: id,email,full_name,role,is_active,created_at,updated_at
    - classes: id,name,trainer_id,start_time,end_time,capacity
    - bookings: id,user_id,class_id,booked_at,status
    - workouts: id,user_id,date,type,duration_minutes,notes
    - payments: id,user_id,reference,amount,currency,status,created_at
    """
    if admin_user.role != "admin":
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    dataset = type.lower().strip()
    headers: List[str]
    rows: List[Tuple] = []

    if dataset == "users":
        headers = ["id", "email", "full_name", "role", "is_active", "created_at", "updated_at"]
        data = db.execute(
            select(
                Users.id,
                Users.email,
                Users.full_name,
                Users.role,
                Users.is_active,
                Users.created_at,
                Users.updated_at,
            ).order_by(Users.id.asc())
        ).all()
        rows = [tuple(r) for r in data]

    elif dataset == "classes":
        headers = ["id", "name", "trainer_id", "start_time", "end_time", "capacity"]
        stmt = select(
            Classes.id,
            Classes.name,
            Classes.trainer_id,
            Classes.start_time,
            Classes.end_time,
            Classes.capacity,
        )
        conditions = []
        if from_date is not None:
            # Cast date to datetime start-of-day filter against start_time
            conditions.append(Classes.start_time >= datetime.combine(from_date, datetime.min.time()))
        if to_date is not None:
            conditions.append(Classes.start_time <= datetime.combine(to_date, datetime.max.time()))
        if conditions:
            stmt = stmt.where(and_(*conditions))
        data = db.execute(stmt.order_by(Classes.start_time.asc(), Classes.id.asc())).all()
        rows = [tuple(r) for r in data]

    elif dataset == "bookings":
        headers = ["id", "user_id", "class_id", "booked_at", "status"]
        data = db.execute(
            select(
                Bookings.id,
                Bookings.user_id,
                Bookings.class_id,
                Bookings.booked_at,
                Bookings.status,
            ).order_by(Bookings.booked_at.desc(), Bookings.id.desc())
        ).all()
        rows = [tuple(r) for r in data]

    elif dataset == "workouts":
        headers = ["id", "user_id", "date", "type", "duration_minutes", "notes"]
        stmt = select(
            Workouts.id,
            Workouts.user_id,
            Workouts.date,
            Workouts.type,
            Workouts.duration_minutes,
            Workouts.notes,
        )
        conditions = []
        if from_date is not None:
            conditions.append(Workouts.date >= from_date)
        if to_date is not None:
            conditions.append(Workouts.date <= to_date)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        data = db.execute(stmt.order_by(Workouts.date.desc(), Workouts.id.desc())).all()
        rows = [tuple(r) for r in data]

    elif dataset == "payments":
        headers = [
            "id",
            "user_id",
            "reference",
            "amount",
            "currency",
            "status",
            "created_at",
        ]
        stmt = select(
            Payments.id,
            Payments.user_id,
            Payments.reference,
            Payments.amount,
            Payments.currency,
            Payments.status,
            Payments.created_at,
        )
        conditions = []
        if from_date is not None:
            conditions.append(Payments.created_at >= datetime.combine(from_date, datetime.min.time()))
        if to_date is not None:
            conditions.append(Payments.created_at <= datetime.combine(to_date, datetime.max.time()))
        if status_filter:
            conditions.append(Payments.status == status_filter)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        data = db.execute(
            stmt.order_by(
                Payments.created_at.desc(),
                Payments.id.desc(),
            )
        ).all()
        rows = [tuple(r) for r in data]

    else:
        raise HTTPException(status_code=400, detail="Unsupported export type. Use one of: users, classes, bookings, workouts, payments.")

    csv_text = _csv_from_rows(headers, rows)

    # Return CSV text with proper content type and a suggestive filename via header
    filename = f"{dataset}_export_{int(datetime.utcnow().timestamp())}.csv"
    return PlainTextResponse(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )
