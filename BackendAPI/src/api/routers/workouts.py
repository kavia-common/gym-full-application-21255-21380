"""
Workouts Router: trainers can create/assign workouts to members, members can view their workouts,
add/update notes/progress, and fetch history with pagination.

Endpoints:
- POST   /workouts                  (trainer/admin) Create/assign a workout to a member
- GET    /workouts/me               (member/trainer/admin) List current user's workouts (with pagination)
- PATCH  /workouts/{id}             (owner=member or trainer/admin) Update notes/progress of a workout
- GET    /workouts/history          (member/trainer/admin) Paginated history for current user (by date desc)
- GET    /workouts/member/{user_id} (trainer/admin) Paginated workouts for specific member (their client)

Notes:
- Aligns with src/api/models.py Workouts schema.
- Uses deps for DB sessions, current user, and role guards.
"""

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, conint
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..deps import db_session as get_db, get_current_user, require_roles
from ..models import Users, Workouts

router = APIRouter(prefix="/workouts", tags=["Workouts"])


# ========= Schemas =========

class WorkoutCreateRequest(BaseModel):
    member_id: int = Field(..., description="User ID of the member to assign the workout to")
    date: date = Field(..., description="Workout date")
    type: str = Field(..., min_length=1, max_length=100, description="Workout type (e.g., 'Cardio', 'Strength')")
    duration_minutes: conint(ge=0) = Field(0, description="Duration of workout in minutes")
    notes: Optional[str] = Field(None, description="Initial notes or instructions")


class WorkoutResponse(BaseModel):
    id: int
    user_id: int
    date: date
    type: str
    duration_minutes: int
    notes: Optional[str]


class WorkoutListResponse(BaseModel):
    total: int = Field(..., description="Total workouts matching criteria")
    page: int = Field(..., description="Current page (1-based)")
    page_size: int = Field(..., description="Page size")
    items: List[WorkoutResponse]


class WorkoutPatchRequest(BaseModel):
    """Partial update for a workout by member (owner) or trainer/admin."""
    date: Optional[date] = Field(None, description="Update workout date")
    type: Optional[str] = Field(None, description="Update workout type")
    duration_minutes: Optional[conint(ge=0)] = Field(None, description="Update workout duration")
    notes: Optional[str] = Field(None, description="Add/update workout notes/progress")


def _to_response(w: Workouts) -> WorkoutResponse:
    return WorkoutResponse(
        id=w.id,
        user_id=w.user_id,
        date=w.date,
        type=w.type,
        duration_minutes=w.duration_minutes,
        notes=w.notes,
    )


def _ensure_user_exists(db: Session, user_id: int) -> Users:
    u = db.get(Users, user_id)
    if not u:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return u


# ========= Routes =========

# PUBLIC_INTERFACE
@router.post(
    "",
    summary="Create/assign a workout to a member",
    description=(
        "Trainers or admins can create and assign a workout to a member. "
        "Members cannot assign workouts."
    ),
    response_model=WorkoutResponse,
    dependencies=[Depends(require_roles(["trainer", "admin"]))],
)
def create_workout(
    payload: WorkoutCreateRequest,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
) -> WorkoutResponse:
    """
    Create a workout record for a member. Only trainers or admins are allowed.
    """
    member = _ensure_user_exists(db, payload.member_id)
    if member.role not in ("member", "trainer", "admin"):
        # Unlikely with current roles; keep defensive
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid target user role")

    w = Workouts(
        user_id=member.id,
        date=payload.date,
        type=payload.type,
        duration_minutes=int(payload.duration_minutes),
        notes=payload.notes,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return _to_response(w)


# PUBLIC_INTERFACE
@router.get(
    "/me",
    summary="List workouts for current user",
    description="Returns paginated workouts for the authenticated user. Sorts by date desc then id desc.",
    response_model=WorkoutListResponse,
)
def list_my_workouts(
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
    page: conint(ge=1) = Query(1, description="Page number (1-based)"),
    page_size: conint(ge=1, le=100) = Query(20, description="Page size (1-100)"),
    from_date: Optional[date] = Query(None, description="Filter: date >= from_date"),
    to_date: Optional[date] = Query(None, description="Filter: date <= to_date"),
) -> WorkoutListResponse:
    """
    Paginated workouts for the current user with optional date range filters.
    """
    stmt = select(Workouts).where(Workouts.user_id == current_user.id)
    cnt = select(func.count(Workouts.id)).where(Workouts.user_id == current_user.id)

    if from_date is not None:
        stmt = stmt.where(Workouts.date >= from_date)
        cnt = cnt.where(Workouts.date >= from_date)
    if to_date is not None:
        stmt = stmt.where(Workouts.date <= to_date)
        cnt = cnt.where(Workouts.date <= to_date)

    total = int(db.execute(cnt).scalar_one() or 0)

    offset = (page - 1) * page_size
    stmt = stmt.order_by(Workouts.date.desc(), Workouts.id.desc()).offset(offset).limit(page_size)
    rows = db.execute(stmt).scalars().all()
    items = [_to_response(w) for w in rows]

    return WorkoutListResponse(total=total, page=page, page_size=page_size, items=items)


# PUBLIC_INTERFACE
@router.get(
    "/member/{user_id}",
    summary="List workouts for a specific member (trainer/admin)",
    description="Trainers/Admins can list workouts for a specific user. Supports pagination and date filters.",
    response_model=WorkoutListResponse,
    dependencies=[Depends(require_roles(["trainer", "admin"]))],
)
def list_member_workouts(
    user_id: int = Path(..., description="Member user id"),
    db: Session = Depends(get_db),
    page: conint(ge=1) = Query(1, description="Page number (1-based)"),
    page_size: conint(ge=1, le=100) = Query(20, description="Page size (1-100)"),
    from_date: Optional[date] = Query(None, description="Filter: date >= from_date"),
    to_date: Optional[date] = Query(None, description="Filter: date <= to_date"),
) -> WorkoutListResponse:
    """
    Paginated workouts for a specified member (only accessible to trainers/admins).
    """
    _ensure_user_exists(db, user_id)
    stmt = select(Workouts).where(Workouts.user_id == user_id)
    cnt = select(func.count(Workouts.id)).where(Workouts.user_id == user_id)

    if from_date is not None:
        stmt = stmt.where(Workouts.date >= from_date)
        cnt = cnt.where(Workouts.date >= from_date)
    if to_date is not None:
        stmt = stmt.where(Workouts.date <= to_date)
        cnt = cnt.where(Workouts.date <= to_date)

    total = int(db.execute(cnt).scalar_one() or 0)
    offset = (page - 1) * page_size
    stmt = stmt.order_by(Workouts.date.desc(), Workouts.id.desc()).offset(offset).limit(page_size)

    rows = db.execute(stmt).scalars().all()
    items = [_to_response(w) for w in rows]

    return WorkoutListResponse(total=total, page=page, page_size=page_size, items=items)


# PUBLIC_INTERFACE
@router.get(
    "/history",
    summary="Workout history for current user",
    description=(
        "Returns paginated workout history for the current user "
        "(alias of /workouts/me with emphasis on 'history')."
    ),
    response_model=WorkoutListResponse,
)
def workout_history(
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
    page: conint(ge=1) = Query(1, description="Page number (1-based)"),
    page_size: conint(ge=1, le=100) = Query(20, description="Page size (1-100)"),
    from_date: Optional[date] = Query(None, description="Filter: date >= from_date"),
    to_date: Optional[date] = Query(None, description="Filter: date <= to_date"),
) -> WorkoutListResponse:
    """
    Equivalent to list_my_workouts; provided for semantic clarity and future extensions.
    """
    return list_my_workouts(
        db=db,
        current_user=current_user,
        page=page,
        page_size=page_size,
        from_date=from_date,
        to_date=to_date,
    )


def _authorize_update(workout: Workouts, user: Users) -> None:
    """
    Member can update only their own workouts.
    Trainer/Admin can update any.
    """
    if user.role in ("trainer", "admin"):
        return
    if workout.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to modify this workout")


# PUBLIC_INTERFACE
@router.patch(
    "/{id}",
    summary="Update a workout (notes/progress and basic fields)",
    description=(
        "Members can update their own workouts "
        "(e.g., notes/progress, duration). "
        "Trainers/Admins can update any workout."
    ),
    response_model=WorkoutResponse,
)
def patch_workout(
    id: int = Path(..., description="Workout ID"),
    payload: WorkoutPatchRequest = None,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
) -> WorkoutResponse:
    """
    Partial update of a workout. Fields supported:
    - date
    - type
    - duration_minutes
    - notes
    """
    w = db.get(Workouts, id)
    if not w:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")

    _authorize_update(w, current_user)

    if payload is None:
        payload = WorkoutPatchRequest()

    changed = False
    if payload.date is not None:
        w.date = payload.date
        changed = True
    if payload.type is not None:
        if not payload.type:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="type cannot be empty")
        w.type = payload.type
        changed = True
    if payload.duration_minutes is not None:
        if payload.duration_minutes < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="duration_minutes must be >= 0")
        w.duration_minutes = int(payload.duration_minutes)
        changed = True
    if payload.notes is not None:
        w.notes = payload.notes
        changed = True

    if changed:
        db.add(w)
        db.commit()
        db.refresh(w)

    return _to_response(w)
