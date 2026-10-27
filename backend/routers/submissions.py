"""Submission history, detail, teacher grading and AI review endpoints."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from starlette.concurrency import run_in_threadpool

try:
    from ..ai_tutor import analyze_code
    from ..auth import get_current_user, get_db
    from ..config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
    from ..database import utcnow
    from ..dependencies import (
        TeacherOnly,
        _assert_submission_visible,
        _load_submission,
        _submission_conditions,
        _submission_detail,
        _teacher_classroom_ids,
    )
    from ..models import (
        ClassroomMembership,
        Problem,
        Submission,
        SubmissionStatusLiteral,
        User,
    )
    from ..schemas import (
        AIAnalyzeResponse,
        SubmissionAIReviewResponse,
        SubmissionDetailOut,
        SubmissionListResponse,
        SubmissionOut,
        TeacherReviewRequest,
    )
except ImportError:
    from ai_tutor import analyze_code  # type: ignore[no-redef]
    from auth import get_current_user, get_db  # type: ignore[no-redef]
    from config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE  # type: ignore[no-redef]
    from database import utcnow  # type: ignore[no-redef]
    from dependencies import (  # type: ignore[no-redef]
        TeacherOnly,
        _assert_submission_visible,
        _load_submission,
        _submission_conditions,
        _submission_detail,
        _teacher_classroom_ids,
    )
    from models import (  # type: ignore[no-redef]
        ClassroomMembership,
        Problem,
        Submission,
        SubmissionStatusLiteral,
        User,
    )
    from schemas import (  # type: ignore[no-redef]
        AIAnalyzeResponse,
        SubmissionAIReviewResponse,
        SubmissionDetailOut,
        SubmissionListResponse,
        SubmissionOut,
        TeacherReviewRequest,
    )

logger = logging.getLogger("codingpass.api")

router = APIRouter(prefix="/api/submissions", tags=["submissions"])


@router.get("", response_model=SubmissionListResponse, summary="Submission history")
async def list_submissions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    problem_id: Optional[int] = Query(default=None, ge=1),
    classroom_id: Optional[int] = Query(default=None, ge=1, description="Restrict to one class."),
    student_id: Optional[int] = Query(default=None, ge=1, description="Teacher/admin only."),
    verdict: Optional[SubmissionStatusLiteral] = Query(default=None, alias="status"),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> SubmissionListResponse:
    """Submission history, newest first."""
    conditions, class_ids = _submission_conditions(db, user)

    if student_id is not None:
        if user.is_student and student_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Students may only read their own submissions.",
            )
        if user.is_teacher:
            enrolled_in_my_classes: set[int] = set()
            if class_ids:
                enrolled_in_my_classes = {
                    int(row[0])
                    for row in db.query(ClassroomMembership.student_id)
                    .filter(ClassroomMembership.classroom_id.in_(class_ids))
                    .all()
                }
            if student_id not in enrolled_in_my_classes and student_id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="That student does not attend one of your classes.",
                )
        conditions.append(Submission.student_id == student_id)

    if classroom_id is not None:
        if not user.is_admin and classroom_id not in class_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not teach that class.",
            )
        conditions.append(
            Submission.problem_id.in_(
                [
                    int(row[0])
                    for row in db.query(Problem.id)
                    .filter(Problem.classroom_id == classroom_id)
                    .all()
                ]
            )
        )

    if problem_id is not None:
        conditions.append(Submission.problem_id == problem_id)
    if verdict is not None:
        conditions.append(Submission.status == verdict)

    total = db.query(func.count(Submission.id)).filter(*conditions).scalar() or 0
    rows = (
        db.query(Submission)
        .options(joinedload(Submission.problem), joinedload(Submission.student))
        .filter(*conditions)
        .order_by(Submission.created_at.desc(), Submission.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    payloads = [
        SubmissionOut(
            id=row.id,
            problem_id=row.problem_id,
            problem_title=row.problem.title if row.problem else "",
            student_id=row.student_id,
            student_username=row.student.username if row.student else "",
            student_name=row.student.display_name if row.student else "",
            language=row.language,
            status=row.status,  # type: ignore[arg-type]
            score=row.score,
            passed_count=row.passed_count,
            total_count=row.total_count,
            execution_time_ms=row.execution_time_ms,
            created_at=row.created_at,
            ai_feedback=row.ai_feedback,
            ai_suggested_score=row.ai_suggested_score,
            ai_model=row.ai_model,
            ai_reviewed_at=row.ai_reviewed_at,
            teacher_feedback=row.teacher_feedback,
            teacher_score=row.teacher_score,
            reviewed_by_id=row.reviewed_by_id,
            reviewed_at=row.reviewed_at,
            rubric_total=row.rubric_total,
        )
        for row in rows
    ]
    return SubmissionListResponse(total=int(total), count=len(payloads), submissions=payloads)


@router.get(
    "/{submission_id}",
    response_model=SubmissionDetailOut,
    summary="One submission with its source",
)
async def read_submission(
    submission_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> SubmissionDetailOut:
    """A single recorded attempt, including the submitted source code."""
    submission = _load_submission(db, submission_id)
    _assert_submission_visible(db, submission, user)
    return _submission_detail(submission)


@router.post(
    "/{submission_id}/ai-review",
    response_model=SubmissionAIReviewResponse,
    summary="AI review of a recorded submission",
)
async def ai_review_submission(
    submission_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SubmissionAIReviewResponse:
    """Review one recorded attempt and persist the feedback on its row."""
    submission = _load_submission(db, submission_id)
    _assert_submission_visible(db, submission, user)

    problem = submission.problem
    analysis = await run_in_threadpool(
        analyze_code,
        submission.code,
        submission.language,
        status=submission.status,
        problem_title=problem.title if problem else "",
        problem_description=problem.description if problem else "",
        score=submission.score,
        passed_count=submission.passed_count,
        total_count=submission.total_count,
        for_teacher=bool(user.is_teacher or user.is_admin),
    )

    submission.ai_feedback = analysis["feedback_markdown"]
    submission.ai_suggested_score = analysis["suggested_score"]
    submission.ai_model = analysis["model"]
    submission.ai_reviewed_at = utcnow()
    db.commit()
    db.refresh(submission)
    logger.info(
        "ai review stored for submission %s (model=%s ai_powered=%s)",
        submission.id,
        analysis["model"],
        analysis["ai_powered"],
    )
    detail = _submission_detail(submission)
    return SubmissionAIReviewResponse(
        **detail.model_dump(), analysis=AIAnalyzeResponse(**analysis)
    )


@router.patch(
    "/{submission_id}/review",
    response_model=SubmissionDetailOut,
    summary="Teacher feedback and score override",
)
async def teacher_review_submission(
    submission_id: int,
    payload: TeacherReviewRequest,
    db: Session = Depends(get_db),
    teacher: User = Depends(TeacherOnly),
) -> SubmissionDetailOut:
    """Store manual grading for one attempt."""
    submission = _load_submission(db, submission_id)
    problem = submission.problem
    allowed = teacher.is_admin or (
        problem is not None
        and (
            problem.teacher_id == teacher.id
            or (
                problem.classroom_id is not None
                and problem.classroom_id in _teacher_classroom_ids(db, teacher)
            )
        )
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may not grade submissions for this problem.",
        )

    submission.teacher_score = payload.teacher_score
    submission.teacher_feedback = (payload.teacher_feedback or "").strip() or None
    submission.reviewed_by_id = teacher.id
    submission.reviewed_at = utcnow()
    db.commit()
    db.refresh(submission)
    logger.info(
        "teacher %s graded submission %s (score=%s)",
        teacher.id,
        submission.id,
        submission.teacher_score,
    )
    return _submission_detail(submission)
