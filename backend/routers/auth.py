"""Authentication routes for CodingPass."""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

try:
    from ..auth import (
        create_access_token,
        get_current_user,
        get_db,
        get_optional_user,
        hash_password,
        verify_password_for_login,
    )
    from ..models import Role, User
    from ..schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
except ImportError:
    from auth import (  # type: ignore[no-redef]
        create_access_token,
        get_current_user,
        get_db,
        get_optional_user,
        hash_password,
        verify_password_for_login,
    )
    from models import Role, User  # type: ignore[no-redef]
    from schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut  # type: ignore[no-redef]

logger = logging.getLogger("codingpass.api")

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
async def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    requester: Optional[User] = Depends(get_optional_user),
) -> UserOut:
    """Sign up an account.

    Public registration always produces a student. Passing role as teacher or admin
    requires the Authorization header of a signed-in admin.
    """
    if payload.role != Role.STUDENT and (requester is None or not requester.is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a signed-in admin can create teacher or admin accounts.",
        )

    if db.query(User.id).filter(User.username == payload.username).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="That username is already taken."
        )
    if db.query(User.id).filter(User.email == payload.email).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email address is already registered.",
        )

    digest = await run_in_threadpool(hash_password, payload.password)
    user = User(
        username=payload.username,
        email=payload.email,
        hashed_password=digest,
        role=payload.role,
        full_name=payload.full_name,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That username or email was taken while registering; please retry.",
        ) from None
    db.refresh(user)
    logger.info("registered %s account '%s' (id=%s)", user.role, user.username, user.id)
    return UserOut.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange credentials for a JWT",
)
async def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Log in with a username or an email address and a password."""
    identifier = (payload.username or "").strip().lower()
    user = db.query(User).filter(func.lower(User.username) == identifier).one_or_none()
    if user is None:
        user = db.query(User).filter(func.lower(User.email) == identifier).one_or_none()

    password_ok = await run_in_threadpool(
        verify_password_for_login, payload.password, user.hashed_password if user else None
    )
    if user is None or not password_ok:
        logger.info("failed login attempt for %r", identifier)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, expires_in = create_access_token(user)
    logger.info("issued a token to %s (role=%s)", user.username, user.role)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
    )


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Log in with an OAuth2 password form (Swagger UI)",
)
async def login_form(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Form-encoded twin of /api/auth/login used by Swagger UI Authorize button."""
    identifier = (form_data.username or "").strip().lower()
    user = db.query(User).filter(func.lower(User.username) == identifier).one_or_none()
    if user is None:
        user = db.query(User).filter(func.lower(User.email) == identifier).one_or_none()

    password_ok = await run_in_threadpool(
        verify_password_for_login, form_data.password, user.hashed_password if user else None
    )
    if user is None or not password_ok:
        logger.info("failed form login attempt for %r", identifier)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, expires_in = create_access_token(user)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut, summary="Current account")
async def read_me(user: User = Depends(get_current_user)) -> UserOut:
    """Return the account the presented token belongs to."""
    return UserOut.model_validate(user)
