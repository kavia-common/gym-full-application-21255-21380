"""
Bookings Router: booking and cancellation with capacity enforcement.

- Members can book and cancel their own bookings.
- Trainers/Admins can also book on behalf of members by specifying member_id (admin only for on-behalf).
- Enforces UNIQUE(user_id, class_id) via ORM and returns friendly errors.
- Checks capacity against current number of bookings with status 'booked'.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..deps import db_session as get_db, get_current_user
from ..models import Users, Classes, Bookings

router = APIRouter(prefix="/bookings", tags=["Bookings"])


class BookingCreateRequest(BaseModel):
    class_id: int = Field(..., description="Class to book")
    # Admin can book for others; members ignore this field
    member_id: Optional[int] = Field(None, description="User ID to book for (admin only)")


class BookingCancelRequest(BaseModel):
    class_id: int = Field(..., description="Class to cancel")


def _get_booked_count(db: Session, class_id: int) -> int:
    # Count only active "booked" status
    count = db.execute(
        select(func.count(Bookings.id)).where(
            Bookings.class_id == class_id,
            Bookings.status == "booked",
        )
    ).scalar_one()
    return int(count or 0)


def _ensure_class_exists(db: Session, class_id: int) -> Classes:
    cls = db.get(Classes, class_id)
    if not cls:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
    return cls


# PUBLIC_INTERFACE
@router.post(
    "",
    summary="Create a booking",
    description="Create a booking for a class, enforcing capacity and unique user/class booking.",
)
def create_booking(
    payload: BookingCreateRequest,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
) -> dict:
    cls = _ensure_class_exists(db, payload.class_id)

    # Resolve target user
    target_user_id = current_user.id
    if payload.member_id is not None:
        # Only admin can book on behalf of someone else
        if current_user.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can book for others")
        # Verify user exists
        target_user = db.get(Users, payload.member_id)
        if not target_user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found")
        target_user_id = target_user.id

    # Capacity check (count only active 'booked')
    current_count = _get_booked_count(db, cls.id)
    if current_count >= cls.capacity:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Class is full")

    # Check existing booking
    existing = db.execute(
        select(Bookings).where(
            Bookings.user_id == target_user_id,
            Bookings.class_id == cls.id,
            Bookings.status == "booked",
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already booked for this class")

    # Create booking
    booking = Bookings(user_id=target_user_id, class_id=cls.id, status="booked")
    db.add(booking)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Unique violation fallback
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already booked for this class")

    db.refresh(booking)
    new_count = _get_booked_count(db, cls.id)
    return {
        "status": "ok",
        "booking_id": booking.id,
        "class_id": cls.id,
        "user_id": target_user_id,
        "booked_count": new_count,
        "capacity": cls.capacity,
    }


# PUBLIC_INTERFACE
@router.post(
    "/cancel",
    summary="Cancel a booking",
    description="Cancel a booking for the current user (or admin can cancel for another user).",
)
def cancel_booking(
    payload: BookingCancelRequest,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
) -> dict:
    cls = _ensure_class_exists(db, payload.class_id)

    # Find existing active booking
    booking = db.execute(
        select(Bookings).where(
            Bookings.class_id == cls.id,
            Bookings.user_id == current_user.id,
            Bookings.status == "booked",
        )
    ).scalar_one_or_none()

    if not booking:
        # If not found, allow admin to cancel any user's booking by flipping any 'booked' record
        if current_user.role == "admin":
            booking = db.execute(
                select(Bookings).where(
                    Bookings.class_id == cls.id,
                    Bookings.status == "booked",
                )
            ).scalar_one_or_none()
        if not booking:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active booking not found")

    # Authorization: member can cancel their own; admin can cancel any
    if current_user.role != "admin" and booking.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot cancel others' bookings")

    # Mark as canceled
    db.execute(
        update(Bookings).where(Bookings.id == booking.id).values(status="canceled")
    )
    db.commit()

    new_count = _get_booked_count(db, cls.id)
    return {
        "status": "ok",
        "canceled_booking_id": booking.id,
        "class_id": cls.id,
        "booked_count": new_count,
        "capacity": cls.capacity,
    }
