"""AI Tutor status probe and ad-hoc code analysis endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

try:
    from ..ai_tutor import analyze_code, check_ai_status
    from ..auth import get_db, get_optional_user
    from ..dependencies import _assert_problem_visible, _load_problem
    from ..models import User
    from ..schemas import AIAnalyzeRequest, AIAnalyzeResponse, AIStatusResponse
except ImportError:
    from ai_tutor import analyze_code, check_ai_status  # type: ignore[no-redef]
    from auth import get_db, get_optional_user  # type: ignore[no-redef]
    from dependencies import _assert_problem_visible, _load_problem  # type: ignore[no-redef]
    from models import User  # type: ignore[no-redef]
    from schemas import AIAnalyzeRequest, AIAnalyzeResponse, AIStatusResponse  # type: ignore[no-redef]

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get(
    "/status",
    response_model=AIStatusResponse,
    summary="Is the AI tutor reachable?",
)
async def ai_status() -> AIStatusResponse:
    """Report whether the vLLM tutor answers, or whether the heuristic will."""
    report = await run_in_threadpool(check_ai_status)
    return AIStatusResponse(**report)


@router.post(
    "/analyze",
    response_model=AIAnalyzeResponse,
    summary="Review a source file without grading it",
)
async def ai_analyze(
    payload: AIAnalyzeRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
) -> AIAnalyzeResponse:
    """Return a structured review of payload.code without storing anything."""
    title = ""
    description = ""
    if payload.problem_id is not None:
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sign in to review code against a problem.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        problem = _load_problem(db, payload.problem_id)
        _assert_problem_visible(problem, user, db)
        title, description = problem.title, problem.description

    for_teacher = bool(user and (user.is_teacher or user.is_admin))
    analysis = await run_in_threadpool(
        analyze_code,
        payload.code,
        payload.language,
        status=payload.status or "unknown",
        compile_output=payload.compile_output or "",
        stderr=payload.stderr or "",
        problem_title=title,
        problem_description=description,
        for_teacher=for_teacher,
    )
    return AIAnalyzeResponse(**analysis)
