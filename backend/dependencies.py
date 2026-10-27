"""Common dependencies and database query helpers for the CodingPass backend."""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, joinedload, selectinload

try:
    from .auth import (
        create_access_token,
        get_current_user,
        get_db,
        get_optional_user,
        hash_password,
        require_roles,
        verify_password_for_login,
    )
    from .config import (
        INVITE_CODE_ALPHABET,
        INVITE_CODE_LENGTH,
        MAX_CONCURRENT_RUNS,
    )
    from .models import (
        Classroom,
        ClassroomMembership,
        Problem,
        Role,
        Submission,
        TestCase,
        User,
    )
    from .schemas import (
        ClassroomDetail,
        ClassroomMemberOut,
        ClassroomOut,
        ProblemDetail,
        ProblemSummary,
        RubricScores,
        SubmissionDetailOut,
        TestCasePublic,
    )
    from .services.judge import _wording_list
except ImportError:
    from auth import (  # type: ignore[no-redef]
        create_access_token,
        get_current_user,
        get_db,
        get_optional_user,
        hash_password,
        require_roles,
        verify_password_for_login,
    )
    from config import (  # type: ignore[no-redef]
        INVITE_CODE_ALPHABET,
        INVITE_CODE_LENGTH,
        MAX_CONCURRENT_RUNS,
    )
    from models import (  # type: ignore[no-redef]
        Classroom,
        ClassroomMembership,
        Problem,
        Role,
        Submission,
        TestCase,
        User,
    )
    from schemas import (  # type: ignore[no-redef]
        ClassroomDetail,
        ClassroomMemberOut,
        ClassroomOut,
        ProblemDetail,
        ProblemSummary,
        RubricScores,
        SubmissionDetailOut,
        TestCasePublic,
    )
    from services.judge import _wording_list  # type: ignore[no-redef]

logger = logging.getLogger("codingpass.api")

StudentOnly = require_roles(Role.STUDENT)
TeacherOnly = require_roles(Role.TEACHER, Role.ADMIN)
AdminOnly = require_roles(Role.ADMIN)


def _execution_slots(request: Request) -> asyncio.Semaphore:
    """Return the process-wide judge semaphore."""
    slots: Optional[asyncio.Semaphore] = getattr(request.app.state, "run_slots", None)
    if slots is None:
        slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
        request.app.state.run_slots = slots
    return slots


# --------------------------------------------------------------------------- #
# Visibility / Authorisation Helpers
# --------------------------------------------------------------------------- #


def _student_classroom_ids(db: Session, student: User) -> list[int]:
    """Classroom ids the student is enrolled in."""
    rows = (
        db.query(ClassroomMembership.classroom_id)
        .filter(ClassroomMembership.student_id == student.id)
        .all()
    )
    return [int(row[0]) for row in rows]


def _teacher_classroom_ids(db: Session, teacher: User) -> list[int]:
    rows = db.query(Classroom.id).filter(Classroom.teacher_id == teacher.id).all()
    return [int(row[0]) for row in rows]


def _problem_conditions(db: Session, user: User) -> list:
    """SQL restrictions that express 'what this user may see' for problems."""
    if user.is_admin:
        return []
    if user.is_teacher:
        owned = _teacher_classroom_ids(db, user)
        allowed = [Problem.teacher_id == user.id]
        if owned:
            allowed.append(Problem.classroom_id.in_(owned))
        return [or_(*allowed)]
    return [Problem.classroom_id.in_(_student_classroom_ids(db, user))]


def _can_manage_problem(problem: Problem, user: User) -> bool:
    """True when user may edit the problem or inspect its hidden cases."""
    return user.is_admin or problem.teacher_id == user.id


def _assert_problem_visible(problem: Problem, user: User, db: Session) -> None:
    """Raise 403 when user may not open problem."""
    if user.is_admin or problem.teacher_id == user.id:
        return
    if user.is_teacher:
        if problem.classroom_id and problem.classroom_id in _teacher_classroom_ids(db, user):
            return
        logger.info("user %s (teacher) has no rights on problem %s", user.id, problem.id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This problem belongs to another teacher's course.",
        )
    if problem.classroom_id and problem.classroom_id in _student_classroom_ids(db, user):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "This problem is not part of a class you joined"
            + (" yet" if not problem.classroom_id else "")
            + "."
        ),
    )


def _assert_classroom_visible(classroom: Classroom, user: User) -> None:
    """Raise 403 when user neither teaches, owns nor attends classroom."""
    if user.is_admin or classroom.teacher_id == user.id:
        return
    if classroom.has_student(user.id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You are not a member of this class.",
    )


def _load_problem(db: Session, problem_id: int) -> Problem:
    """Fetch a problem with its teacher and test cases, or raise 404."""
    problem = (
        db.query(Problem)
        .options(joinedload(Problem.teacher), selectinload(Problem.test_cases))
        .filter(Problem.id == problem_id)
        .one_or_none()
    )
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found."
        )
    return problem


def _load_classroom(db: Session, classroom_id: int) -> Classroom:
    """Fetch a classroom with its roster loaded, or raise 404."""
    classroom = (
        db.query(Classroom)
        .options(joinedload(Classroom.teacher), selectinload(Classroom.memberships))
        .filter(Classroom.id == classroom_id)
        .one_or_none()
    )
    if classroom is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Class not found."
        )
    return classroom


def _generate_class_code(db: Session) -> str:
    """Invent an unused invite code."""
    for _ in range(25):
        candidate = "".join(
            secrets.choice(INVITE_CODE_ALPHABET) for _ in range(INVITE_CODE_LENGTH)
        )
        if db.query(Classroom.id).filter(Classroom.code == candidate).first() is None:
            return candidate
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Could not allocate a unique class code; supply one explicitly.",
    )


# --------------------------------------------------------------------------- #
# Response / Entity Builders
# --------------------------------------------------------------------------- #


def _problem_counts(db: Session, problem_ids: list[int]) -> dict[int, tuple[int, int]]:
    """Map problem id -> (sample_count, hidden_count) in one query."""
    if not problem_ids:
        return {}
    rows = (
        db.query(
            TestCase.problem_id,
            func.sum(case((TestCase.is_sample.is_(True), 1), else_=0)),
            func.sum(case((TestCase.is_sample.is_(False), 1), else_=0)),
        )
        .filter(TestCase.problem_id.in_(problem_ids))
        .group_by(TestCase.problem_id)
        .all()
    )
    return {int(pid): (int(samples or 0), int(hidden or 0)) for pid, samples, hidden in rows}


def _classroom_counts(
    db: Session, classroom_ids: list[int]
) -> tuple[dict[int, int], dict[int, int]]:
    """Return (student_counts, problem_counts) keyed by classroom id."""
    if not classroom_ids:
        return {}, {}
    student_rows = (
        db.query(ClassroomMembership.classroom_id, func.count(ClassroomMembership.id))
        .filter(ClassroomMembership.classroom_id.in_(classroom_ids))
        .group_by(ClassroomMembership.classroom_id)
        .all()
    )
    problem_rows = (
        db.query(Problem.classroom_id, func.count(Problem.id))
        .filter(Problem.classroom_id.in_(classroom_ids))
        .group_by(Problem.classroom_id)
        .all()
    )
    return (
        {int(cid): int(count) for cid, count in student_rows},
        {int(cid): int(count) for cid, count in problem_rows},
    )


def _problem_summary(problem: Problem, counts: dict[int, tuple[int, int]]) -> ProblemSummary:
    samples, hidden = counts.get(problem.id, (0, 0))
    return ProblemSummary(
        id=problem.id,
        title=problem.title,
        difficulty=problem.difficulty,  # type: ignore[arg-type]
        time_limit_sec=problem.time_limit_sec,
        memory_limit_mb=problem.memory_limit_mb,
        classroom_id=problem.classroom_id,
        teacher_id=problem.teacher_id,
        teacher_name=problem.teacher.display_name if problem.teacher else "",
        created_at=problem.created_at,
        sample_count=samples,
        hidden_count=hidden,
        problem_code=problem.problem_code,
        has_ref_code=problem.has_ref_code,
        has_wording_rules=problem.has_wording_rules,
        has_random_generator=problem.has_random_generator,
        has_statement_pdf=problem.has_statement_pdf,
    )


def _problem_detail(problem: Problem, reveal_hidden: bool) -> ProblemDetail:
    """Full problem payload."""
    visible_cases = problem.test_cases if reveal_hidden else problem.sample_test_cases
    return ProblemDetail(
        id=problem.id,
        title=problem.title,
        difficulty=problem.difficulty,  # type: ignore[arg-type]
        time_limit_sec=problem.time_limit_sec,
        memory_limit_mb=problem.memory_limit_mb,
        classroom_id=problem.classroom_id,
        teacher_id=problem.teacher_id,
        teacher_name=problem.teacher.display_name if problem.teacher else "",
        created_at=problem.created_at,
        sample_count=len(problem.sample_test_cases),
        hidden_count=len(problem.hidden_test_cases),
        problem_code=problem.problem_code,
        has_ref_code=problem.has_ref_code,
        has_wording_rules=problem.has_wording_rules,
        has_random_generator=problem.has_random_generator,
        has_statement_pdf=problem.has_statement_pdf,
        description=problem.description,
        starter_code_c=problem.starter_code_c,
        starter_code_cpp=problem.starter_code_cpp,
        pdf_path=problem.pdf_path if reveal_hidden else None,
        test_cases=[
            TestCasePublic(
                id=case_row.id,
                input=case_row.input,
                expected_output=case_row.expected_output,
                is_sample=case_row.is_sample,
            )
            for case_row in visible_cases
        ],
        wording_rules=problem.wording_rules if reveal_hidden else None,
    )


def _classroom_out(
    classroom: Classroom,
    student_counts: dict[int, int],
    problem_counts: dict[int, int],
) -> ClassroomOut:
    return ClassroomOut(
        id=classroom.id,
        name=classroom.name,
        code=classroom.code,
        description=classroom.description,
        teacher_id=classroom.teacher_id,
        teacher_name=classroom.teacher.display_name if classroom.teacher else "",
        created_at=classroom.created_at,
        student_count=student_counts.get(classroom.id, 0),
        problem_count=problem_counts.get(classroom.id, 0),
    )


def _classroom_detail(db: Session, classroom: Classroom) -> ClassroomDetail:
    """Classroom summary plus roster and problem set."""
    memberships = (
        db.query(ClassroomMembership)
        .options(joinedload(ClassroomMembership.student))
        .filter(ClassroomMembership.classroom_id == classroom.id)
        .order_by(ClassroomMembership.joined_at, ClassroomMembership.id)
        .all()
    )
    problems = (
        db.query(Problem)
        .options(joinedload(Problem.teacher))
        .filter(Problem.classroom_id == classroom.id)
        .order_by(Problem.created_at, Problem.id)
        .all()
    )
    counts = _problem_counts(db, [problem.id for problem in problems])
    student_counts, problem_counts = _classroom_counts(db, [classroom.id])
    base = _classroom_out(classroom, student_counts, problem_counts)
    return ClassroomDetail(
        **base.model_dump(),
        teacher_username=classroom.teacher.username if classroom.teacher else "",
        students=[
            ClassroomMemberOut(
                id=membership.student.id,
                username=membership.student.username,
                full_name=membership.student.full_name,
                email=membership.student.email,
                joined_at=membership.joined_at,
            )
            for membership in memberships
        ],
        problems=[_problem_summary(problem, counts) for problem in problems],
    )


# --------------------------------------------------------------------------- #
# Submissions Loaders & Helpers
# --------------------------------------------------------------------------- #


def _load_submission(db: Session, submission_id: int) -> Submission:
    """Load one submission with its problem and author preloaded, or 404."""
    submission = (
        db.query(Submission)
        .options(joinedload(Submission.problem), joinedload(Submission.student))
        .filter(Submission.id == submission_id)
        .one_or_none()
    )
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found."
        )
    return submission


def _assert_submission_visible(db: Session, submission: Submission, user: User) -> None:
    """Raise 403 unless user may read submission."""
    problem = submission.problem
    allowed = user.is_admin or submission.student_id == user.id
    if not allowed and problem is not None:
        allowed = problem.teacher_id == user.id or (
            problem.classroom_id is not None
            and problem.classroom_id in _teacher_classroom_ids(db, user)
        )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may not review this submission.",
        )


def _submission_conditions(db: Session, user: User) -> tuple[list, list[int]]:
    """Return (conditions, manageable_class_ids) for submission history."""
    if user.is_admin:
        return [], []
    if user.is_teacher:
        class_ids = _teacher_classroom_ids(db, user)
        enrolled: list[int] = []
        if class_ids:
            enrolled = [
                int(row[0])
                for row in db.query(ClassroomMembership.student_id)
                .filter(ClassroomMembership.classroom_id.in_(class_ids))
                .distinct()
                .all()
            ]
        own_problem_ids = [
            int(row[0])
            for row in db.query(Problem.id)
            .filter(
                or_(
                    Problem.teacher_id == user.id,
                    Problem.classroom_id.in_(class_ids) if class_ids else False,
                )
            )
            .all()
        ]
        conditions = [Submission.problem_id.in_(own_problem_ids)]
        return conditions, class_ids
    return [Submission.student_id == user.id], _student_classroom_ids(db, user)


def _submission_detail(submission: Submission) -> SubmissionDetailOut:
    """Serialise one submission for review endpoints."""
    problem = submission.problem
    return SubmissionDetailOut(
        id=submission.id,
        problem_id=submission.problem_id,
        problem_title=problem.title if problem else "",
        student_id=submission.student_id,
        student_username=submission.student.username if submission.student else "",
        student_name=submission.student.display_name if submission.student else "",
        language=submission.language,
        status=submission.status,  # type: ignore[arg-type]
        score=submission.score,
        passed_count=submission.passed_count,
        total_count=submission.total_count,
        execution_time_ms=submission.execution_time_ms,
        created_at=submission.created_at,
        code=submission.code,
        ai_feedback=submission.ai_feedback,
        ai_suggested_score=submission.ai_suggested_score,
        ai_model=submission.ai_model,
        ai_reviewed_at=submission.ai_reviewed_at,
        teacher_feedback=submission.teacher_feedback,
        teacher_score=submission.teacher_score,
        reviewed_by_id=submission.reviewed_by_id,
        reviewed_at=submission.reviewed_at,
        rubric_total=submission.rubric_total,
        rubric=RubricScores(
            compile_score=submission.compile_score,
            sample_score=submission.sample_score,
            random_score=submission.random_score,
            logic_score=submission.logic_score,
            quality_score=submission.quality_score,
            total=submission.rubric_total,
        ),
        presentation_warning=bool(submission.presentation_warning),
        wording_pass=bool(submission.wording_pass),
        wording_violations=_wording_list(submission.wording_violations),
    )


def _gradebook_effective_score(submission: Submission) -> float:
    """Score one attempt counts for in the gradebook."""
    return submission.teacher_score if submission.teacher_score is not None else submission.score
