"""
Classes Router: CRUD for gym classes and public listing with filters.

- Admins can create, update, delete any class.
- Trainers can create classes for themselves and update/delete only their own classes.
- Members can list classes.

Listing supports filters: date range, trainer_id, pagination.
"""

from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Path, status
from pydantic import BaseModel, Field, conint
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from ..deps import db_session as get_db, get_current_user, require_roles
from ..models import Users, Classes

router = APIRouter(prefix="/classes", tags=["Classes"])


# ====== Schemas ======

class ClassBase(BaseModel):
    name: str = Field(..., description="Class name")
    description: Optional[str] = Field(None, description="Class description")
    start_time: datetime = Field(..., description="Start time (ISO8601)")
    end_time: datetime = Field(..., description="End time (ISO8601)")
    capacity: conint(ge=1) = Field(10, description="Max capacity (>=1)")


class ClassCreateRequest(ClassBase):
    trainer_id: Optional[int] = Field(None, description="Trainer user id; defaults to current trainer/admin")


class ClassUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, description="Class name")
    description: Optional[str] = Field(None, description="Class description")
    start_time: Optional[datetime] = Field(None, description="Start time")
    end_time: Optional[datetime] = Field(None, description="End time")
    capacity: Optional[conint(ge=1)] = Field(None, description="Max capacity")
    trainer_id: Optional[int] = Field(None, description="Trainer user id (admin only)")


class ClassListItem(BaseModel):
    id: int
    name: str
    description: Optional[str]
    trainer_id: Optional[int]
    start_time: datetime
    end_time: datetime
    capacity: int
    booked_count: int = Field(..., description="Current number of bookings for this class")


class ClassListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[ClassListItem]


class ClassDetailResponse(ClassListItem):
    pass


def _visible_from_row(row: Tuple[Classes, int]) -> ClassListItem:
    item, booked_count = row
    return ClassListItem(
        id=item.id,
        name=item.name,
        description=item.description,
        trainer_id=item.trainer_id,
        start_time=item.start_time,
        end_time=item.end_time,
        capacity=item.capacity,
        booked_count=booked_count or 0,
    )


# ====== Helpers ======

def _assert_time_order(start_time: datetime, end_time: datetime) -> None:
    if end_time <= start_time:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_time must be after start_time")


def _ensure_trainer_or_admin(user: Users) -> None:
    if user.role not in ("trainer", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only trainers or admins can manage classes")


def _ensure_can_mutate_class(target: Classes, user: Users) -> None:
    # Admin can always update/delete; trainer can only modify own classes
    if user.role == "admin":
        return
    if user.role == "trainer" and target.trainer_id == user.id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to modify this class")


# ====== Routes ======

# PUBLIC_INTERFACE
@router.get(
    "",
    summary="List classes",
    description="List classes with optional filters (date range, trainer) and pagination.",
    response_model=ClassListResponse,
)
def list_classes(
    db: Session = Depends(get_db),
    start_from: Optional[datetime] = Query(None, description="Include classes with start_time >= this"),
    start_to: Optional[datetime] = Query(None, description="Include classes with start_time <= this"),
    trainer_id: Optional[int] = Query(None, description="Filter by trainer id"),
    page: conint(ge=1) = Query(1, description="Page number (1-based)"),
    page_size: conint(ge=1, le=100) = Query(20, description="Page size"),
) -> ClassListResponse:
    """
    Returns class list with booked_count aggregation.
    """
    # Build base query with aggregation for booked_count
    bookings_cte = select(
        Classes.id.label("class_id"),
        func.count().label("booked_count"),
    ).select_from(Classes).join(Classes.bookings, isouter=True).group_by(Classes.id).cte(name="classes_counts")

    cls = Classes
    counts = bookings_cte

    stmt = (
        select(cls, func.coalesce(counts.c.booked_count, 0))
        .join(counts, counts.c.class_id == cls.id)
    )

    count_stmt = select(func.count(cls.id))

    # Filters
    where_clauses = []
    if start_from is not None:
        where_clauses.append(cls.start_time >= start_from)
    if start_to is not None:
        where_clauses.append(cls.start_time <= start_to)
    if trainer_id is not None:
        where_clauses.append(cls.trainer_id == trainer_id)

    if where_clauses:
        stmt = stmt.where(and_(*where_clauses))
        count_stmt = count_stmt.where(and_(*where_clauses))

    total = db.execute(count_stmt).scalar_one()
    offset = (page - 1) * page_size
    stmt = stmt.order_by(cls.start_time.asc(), cls.id.asc()).offset(offset).limit(page_size)

    rows = db.execute(stmt).all()
    items = [_visible_from_row(r) for r in rows]

    return ClassListResponse(total=total, page=page, page_size=page_size, items=items)


# PUBLIC_INTERFACE
@router.get(
    "/{id}",
    summary="Get class details",
    description="Returns a single class with current booked count.",
    response_model=ClassDetailResponse,
)
def get_class_detail(
    id: int = Path(..., description="Class ID"),
    db: Session = Depends(get_db),
) -> ClassDetailResponse:
    cls = db.get(Classes, id)
    if not cls:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")

    booked_count = db.execute(
        select(func.count()).select_from(Classes).join(Classes.bookings, isouter=True).where(Classes.id == id)
    ).scalar_one()

    return ClassDetailResponse(
        id=cls.id,
        name=cls.name,
        description=cls.description,
        trainer_id=cls.trainer_id,
        start_time=cls.start_time,
        end_time=cls.end_time,
        capacity=cls.capacity,
        booked_count=booked_count or 0,
    )


# PUBLIC_INTERFACE
@router.post(
    "",
    summary="Create a class",
    description="Create a class. Admins can set any trainer; trainers default to themselves.",
    response_model=ClassDetailResponse,
    dependencies=[Depends(require_roles(["admin", "trainer"]))],
)
def create_class(
    payload: ClassCreateRequest,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
) -> ClassDetailResponse:
    _ensure_trainer_or_admin(current_user)

    start = payload.start_time
    end = payload.end_time
    _assert_time_order(start, end)

    trainer_id = payload.trainer_id
    if current_user.role == "trainer":
        # Trainers can only create for themselves
        trainer_id = current_user.id
    else:
        # Admin: if not provided, allow None (unassigned) or set explicit given id
        trainer_id = trainer_id

    row = Classes(
        name=payload.name,
        description=payload.description,
        trainer_id=trainer_id,
        start_time=start,
        end_time=end,
        capacity=payload.capacity,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return ClassDetailResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        trainer_id=row.trainer_id,
        start_time=row.start_time,
        end_time=row.end_time,
        capacity=row.capacity,
        booked_count=0,
    )


# PUBLIC_INTERFACE
@router.patch(
    "/{id}",
    summary="Update a class",
    description="Admins can update any class; trainers can only update their own classes.",
    response_model=ClassDetailResponse,
    dependencies=[Depends(require_roles(["admin", "trainer"]))],
)
def update_class(
    id: int,
    payload: ClassUpdateRequest,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
) -> ClassDetailResponse:
    row = db.get(Classes, id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")

    _ensure_can_mutate_class(row, current_user)

    if payload.start_time is not None or payload.end_time is not None:
        start = payload.start_time or row.start_time
        end = payload.end_time or row.end_time
        _assert_time_order(start, end)
        row.start_time = start
        row.end_time = end

    if payload.name is not None:
        row.name = payload.name
    if payload.description is not None:
        row.description = payload.description
    if payload.capacity is not None:
        if payload.capacity < 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="capacity must be >= 1")
        row.capacity = int(payload.capacity)

    if payload.trainer_id is not None:
        if current_user.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can change trainer")
        row.trainer_id = payload.trainer_id

    db.add(row)
    db.commit()
    db.refresh(row)

    # booked count
    booked_count = db.execute(
        select(func.count()).select_from(Classes).join(Classes.bookings, isouter=True).where(Classes.id == id)
    ).scalar_one()

    return ClassDetailResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        trainer_id=row.trainer_id,
        start_time=row.start_time,
        end_time=row.end_time,
        capacity=row.capacity,
        booked_count=booked_count or 0,
    )


# PUBLIC_INTERFACE
@router.delete(
    "/{id}",
    summary="Delete a class",
    description="Admins can delete any class; trainers can delete only their own classes.",
    dependencies=[Depends(require_roles(["admin", "trainer"]))],
)
def delete_class(
    id: int,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
) -> dict:
    row = db.get(Classes, id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")

    _ensure_can_mutate_class(row, current_user)

    db.delete(row)
    db.commit()
    return {"status": "ok", "deleted_id": id}
