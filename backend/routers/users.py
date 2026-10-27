"""User management routes for CodingPass administrators."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

try:
    from ..auth import get_current_user, get_db, hash_password
    from ..dependencies import AdminOnly
    from ..models import Role, User
    from ..schemas import UserListResponse, UserOut, UserUpdate
except ImportError:
    from auth import get_current_user, get_db, hash_password  # type: ignore[no-redef]
    from dependencies import AdminOnly  # type: ignore[no-redef]
    from models import Role, User  # type: ignore[no-redef]
    from schemas import UserListResponse, UserOut, UserUpdate  # type: ignore[no-redef]

logger = logging.getLogger("codingpass.api")

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=UserListResponse, summary="List accounts (Admin only)")
async def list_users(
    role: Optional[str] = Query(None, description="Filter by role: admin, teacher, student"),
    search: Optional[str] = Query(None, description="Search username, email, full name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(AdminOnly),
) -> UserListResponse:
    """List all registered users with filtering and pagination."""
    query = db.query(User)

    if role and role in Role.ALL:
        query = query.filter(User.role == role)

    if search:
        needle = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(User.username).like(needle),
                func.lower(User.email).like(needle),
                func.lower(User.full_name).like(needle),
            )
        )

    total = query.count()
    users = query.order_by(User.id.asc()).offset(offset).limit(limit).all()

    return UserListResponse(
        users=[UserOut.model_validate(u) for u in users],
        total=total,
    )


@router.get("/{user_id}", response_model=UserOut, summary="Get user account details")
async def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserOut:
    """Fetch user account info. Non-admins may only view their own account."""
    if not current_user.is_admin and current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only view your own profile.",
        )

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut, summary="Update user account")
async def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserOut:
    """Update user account attributes.

    - Admins can update roles, full name, email, and perform password resets.
    - Non-admins can only update their own full name.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    if not current_user.is_admin and current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only modify your own profile.",
        )

    # Role change guard
    if payload.role is not None:
        if not current_user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators can modify user roles.",
            )
        if user.is_admin and payload.role != Role.ADMIN:
            admin_count = db.query(func.count(User.id)).filter(User.role == Role.ADMIN).scalar()
            if admin_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot demote the last remaining administrator.",
                )
        user.role = payload.role

    # Email update guard
    if payload.email is not None and payload.email != user.email:
        if not current_user.is_admin and current_user.id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied.",
            )
        existing = db.query(User.id).filter(User.email == payload.email, User.id != user_id).first()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That email address is already in use by another account.",
            )
        user.email = payload.email

    # Full name update
    if payload.full_name is not None:
        user.full_name = payload.full_name

    # Admin password reset
    if payload.password is not None:
        if not current_user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators can directly reset user passwords.",
            )
        user.hashed_password = await run_in_threadpool(hash_password, payload.password)

    db.commit()
    db.refresh(user)
    logger.info("updated user account '%s' (id=%s) by '%s'", user.username, user.id, current_user.username)
    return UserOut.model_validate(user)


@router.delete("/{user_id}", summary="Delete user account (Admin only)")
async def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(AdminOnly),
) -> dict:
    """Delete a user account. Cannot delete self or the last administrator."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrators cannot delete their own account.",
        )

    if user.is_admin:
        admin_count = db.query(func.count(User.id)).filter(User.role == Role.ADMIN).scalar()
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete the last remaining administrator.",
            )

    username = user.username
    db.delete(user)
    db.commit()
    logger.info("administrator '%s' deleted user '%s' (id=%s)", admin.username, username, user_id)
    return {"message": f"User '{username}' deleted successfully."}
