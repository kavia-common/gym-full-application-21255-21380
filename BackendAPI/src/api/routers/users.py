"""
Users Router: self profile management (including notification preferences) and admin-only user management.

Routes:
- GET  /users/me
- PATCH /users/me
- GET  /users           (admin; supports filters/pagination)
- PATCH /users/{id}     (admin; update is_active/role)
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Path, status
from pydantic import BaseModel, EmailStr, Field, conint
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..deps import db_session as get_db, get_current_user, require_roles
from ..models import Users

router = APIRouter(prefix="/users", tags=["Users"])


# ========== Schemas ==========

class NotificationPreferences(BaseModel):
    """
    Notification preferences for a user. These are stored as a simple set of boolean flags
    on the profile update payload and are not yet persisted separately. In a future iteration,
    this can be mapped to a dedicated table or JSON column.
    """
    email_notifications: bool = Field(default=True, description="Receive email notifications")
    sms_notifications: bool = Field(default=False, description="Receive SMS notifications")
    push_notifications: bool = Field(default=False, description="Receive push notifications")


class UserMeResponse(BaseModel):
    id: int = Field(..., description="User ID")
    email: EmailStr = Field(..., description="User email")
    full_name: Optional[str] = Field(None, description="Full name")
    role: str = Field(..., description="Role (member/trainer/admin)")
    is_active: bool = Field(..., description="Whether the user is active")
    created_at: str = Field(..., description="Creation timestamp")
    updated_at: str = Field(..., description="Last update timestamp")
    # Echo back preferences when PATCHing; on GET we return defaults
    notification_preferences: Optional[NotificationPreferences] = Field(
        default=None, description="Notification preferences (not persisted yet)"
    )


class UserMePatchRequest(BaseModel):
    full_name: Optional[str] = Field(None, description="Full name")
    # Password change in profile can be added in future iteration
    notification_preferences: Optional[NotificationPreferences] = Field(
        default=None, description="Notification preferences"
    )


class AdminUserListItem(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str]
    role: str
    is_active: bool


class AdminUsersListResponse(BaseModel):
    total: int = Field(..., description="Total items matching filters")
    page: int = Field(..., description="Current page (1-based)")
    page_size: int = Field(..., description="Page size")
    items: List[AdminUserListItem] = Field(..., description="List of users")


class AdminUserPatchRequest(BaseModel):
    is_active: Optional[bool] = Field(None, description="Activate/deactivate user")
    role: Optional[str] = Field(None, description="Change role (member/trainer/admin)")


# ========== Helpers ==========

def _user_to_me_response(user: Users, prefs: Optional[NotificationPreferences] = None) -> UserMeResponse:
    """
    Convert Users ORM object to API response for /users/me.
    Preferences are ephemeral (not persisted) and are included only when provided.
    """
    return UserMeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at.isoformat(),
        updated_at=user.updated_at.isoformat(),
        notification_preferences=prefs,
    )


# ========== Routes ==========

# PUBLIC_INTERFACE
@router.get(
    "/me",
    summary="Get current user profile",
    description=(
        "Returns the profile data for the currently authenticated user. "
        "Notification preferences are not yet persisted and therefore not "
        "returned unless provided in the same session."
    ),
    response_model=UserMeResponse,
)
def get_me(current_user: Users = Depends(get_current_user)) -> UserMeResponse:
    """
    Return the current authenticated user's profile.
    """
    # As preferences persistence is not implemented yet, return None for preferences on GET.
    return _user_to_me_response(current_user, prefs=None)


# PUBLIC_INTERFACE
@router.patch(
    "/me",
    summary="Update current user profile",
    description=(
        "Allows the current user to update profile fields like full_name and "
        "notification preferences. Notification preferences are accepted and "
        "echoed back but are not persisted yet."
    ),
    response_model=UserMeResponse,
)
def patch_me(
    payload: UserMePatchRequest,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user),
) -> UserMeResponse:
    """
    Update profile for the current user. Supports:
    - full_name
    - notification_preferences (accepted/echoed; not persisted)
    """
    changed = False
    if payload.full_name is not None:
        current_user.full_name = payload.full_name
        changed = True

    if changed:
        db.add(current_user)
        db.commit()
        db.refresh(current_user)

    # Echo back provided preferences (not persisted).
    return _user_to_me_response(current_user, prefs=payload.notification_preferences)


# ADMIN: list users with filters & pagination
# PUBLIC_INTERFACE
@router.get(
    "",
    summary="List users (admin)",
    description=(
        "Admin-only endpoint to list users with optional filters and pagination."
    ),
    response_model=AdminUsersListResponse,
    dependencies=[Depends(require_roles(["admin"]))],
)
def list_users(
    db: Session = Depends(get_db),
    # Filters
    email: Optional[str] = Query(None, description="Filter by email (icontains)"),
    role: Optional[str] = Query(None, description="Filter by role"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    # Pagination
    page: conint(ge=1) = Query(1, description="Page number (1-based)"),
    page_size: conint(ge=1, le=100) = Query(20, description="Page size (1-100)"),
) -> AdminUsersListResponse:
    """
    List users with optional filters and pagination. Admin only.
    """
    stmt = select(Users)
    count_stmt = select(func.count(Users.id))

    if email:
        like = f"%{email.lower()}%"
        stmt = stmt.where(func.lower(Users.email).like(like))
        count_stmt = count_stmt.where(func.lower(Users.email).like(like))

    if role:
        stmt = stmt.where(Users.role == role)
        count_stmt = count_stmt.where(Users.role == role)

    if is_active is not None:
        stmt = stmt.where(Users.is_active == is_active)
        count_stmt = count_stmt.where(Users.is_active == is_active)

    total = db.execute(count_stmt).scalar_one()

    offset = (page - 1) * page_size
    stmt = stmt.order_by(Users.id.asc()).offset(offset).limit(page_size)

    rows = db.execute(stmt).scalars().all()
    items = [
        AdminUserListItem(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            role=u.role,
            is_active=u.is_active,
        )
        for u in rows
    ]

    return AdminUsersListResponse(total=total, page=page, page_size=page_size, items=items)


# ADMIN: update is_active/role for a user
# PUBLIC_INTERFACE
@router.patch(
    "/{id}",
    summary="Update user (admin)",
    description="Admin-only endpoint to update a user's is_active or role.",
    dependencies=[Depends(require_roles(["admin"]))],
)
def admin_patch_user(
    id: int = Path(..., description="User ID"),
    payload: AdminUserPatchRequest = None,
    db: Session = Depends(get_db),
) -> dict:
    """
    Update a user's activation status and/or role. Admin only.
    Returns the updated user snapshot.
    """
    user = db.get(Users, id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    updated = False
    if payload is None:
        payload = AdminUserPatchRequest()

    if payload.is_active is not None:
        user.is_active = payload.is_active
        updated = True

    if payload.role is not None:
        if payload.role not in ("member", "trainer", "admin"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role")
        user.role = payload.role
        updated = True

    if updated:
        db.add(user)
        db.commit()
        db.refresh(user)

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }
