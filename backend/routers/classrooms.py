"""Classroom management, gradebook export, and plagiarism screen endpoints."""

from __future__ import annotations

import csv
import io
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload
from starlette.concurrency import run_in_threadpool

try:
    from ..auth import get_current_user, get_db, require_roles
    from ..config import (
        DEFAULT_STARTER_C,
        DEFAULT_STARTER_CPP,
        PLAGIARISM_DEFAULT_THRESHOLD,
        PLAGIARISM_MAX_SUBMISSIONS,
        LanguageLiteral,
        _csv_cell,
    )
    from ..dependencies import (
        StudentOnly,
        TeacherOnly,
        _assert_classroom_visible,
        _can_manage_problem,
        _classroom_counts,
        _classroom_detail,
        _classroom_out,
        _generate_class_code,
        _gradebook_effective_score,
        _load_classroom,
        _load_problem,
        _student_classroom_ids,
    )
    from ..models import (
        Classroom,
        ClassroomMembership,
        Problem,
        Role,
        Submission,
        User,
    )
    from ..plagiarism import compute_similarity_pairs
    from ..schemas import (
        ClassroomCreate,
        ClassroomDetail,
        ClassroomJoinRequest,
        ClassroomOut,
        PlagiarismPair,
        PlagiarismReport,
    )
except ImportError:
    from auth import get_current_user, get_db, require_roles  # type: ignore[no-redef]
    from config import (  # type: ignore[no-redef]
        DEFAULT_STARTER_C,
        DEFAULT_STARTER_CPP,
        PLAGIARISM_DEFAULT_THRESHOLD,
        PLAGIARISM_MAX_SUBMISSIONS,
        LanguageLiteral,
        _csv_cell,
    )
    from dependencies import (  # type: ignore[no-redef]
        StudentOnly,
        TeacherOnly,
        _assert_classroom_visible,
        _can_manage_problem,
        _classroom_counts,
        _classroom_detail,
        _classroom_out,
        _generate_class_code,
        _gradebook_effective_score,
        _load_classroom,
        _load_problem,
        _student_classroom_ids,
    )
    from models import (  # type: ignore[no-redef]
        Classroom,
        ClassroomMembership,
        Problem,
        Role,
        Submission,
        User,
    )
    from plagiarism import compute_similarity_pairs  # type: ignore[no-redef]
    from schemas import (  # type: ignore[no-redef]
        ClassroomCreate,
        ClassroomDetail,
        ClassroomJoinRequest,
        ClassroomOut,
        PlagiarismPair,
        PlagiarismReport,
    )

logger = logging.getLogger("codingpass.api")

router = APIRouter(prefix="/api/classrooms", tags=["classrooms"])


def _plagiarism_label(submission: Submission) -> str:
    """Report label for one submission: recognisable, and unique per student."""
    student = submission.student
    if student is None:
        return f"submission {submission.id}"
    return f"{student.display_name} (@{student.username})"


def _is_scaffolding(code: str, problem: Problem, language: str) -> bool:
    """True when the source is still the untouched starter template."""
    stripped = (code or "").strip()
    if not stripped:
        return True
    templates = {
        problem.starter_for(language).strip(),
        DEFAULT_STARTER_C.strip(),
        DEFAULT_STARTER_CPP.strip(),
    }
    return stripped in templates


@router.get("", response_model=list[ClassroomOut], summary="List my classes")
async def list_classrooms(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[ClassroomOut]:
    """Classes visible to the caller."""
    query = db.query(Classroom).options(joinedload(Classroom.teacher))
    if user.is_admin:
        statement = query
    elif user.is_teacher:
        statement = query.filter(Classroom.teacher_id == user.id)
    else:
        statement = query.filter(Classroom.id.in_(_student_classroom_ids(db, user)))

    classrooms = statement.order_by(Classroom.created_at.desc(), Classroom.id.desc()).all()
    student_counts, problem_counts = _classroom_counts(db, [row.id for row in classrooms])
    return [
        _classroom_out(classroom, student_counts, problem_counts) for classroom in classrooms
    ]


@router.post(
    "",
    response_model=ClassroomOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a class",
)
async def create_classroom(
    payload: ClassroomCreate,
    db: Session = Depends(get_db),
    teacher: User = Depends(TeacherOnly),
) -> ClassroomOut:
    """Create a class and its invite code."""
    owner_id = teacher.id
    if payload.teacher_id is not None:
        if not teacher.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only an admin can assign a class to another teacher.",
            )
        assignee = db.get(User, payload.teacher_id)
        if assignee is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="That teacher does not exist."
            )
        if assignee.role == Role.STUDENT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{assignee.username} is a student and cannot own a class.",
            )
        owner_id = assignee.id

    code = payload.code or _generate_class_code(db)
    if db.query(Classroom.id).filter(Classroom.code == code).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The invite code {code} is already in use.",
        )

    classroom = Classroom(
        name=payload.name,
        code=code,
        description=payload.description,
        teacher_id=owner_id,
    )
    db.add(classroom)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The invite code {code} was claimed first; please pick another.",
        ) from None
    db.refresh(classroom)
    db.refresh(classroom, attribute_names=["teacher"])
    logger.info(
        "teacher id=%s created class %r (%s)", teacher.id, classroom.name, classroom.code
    )
    return _classroom_out(classroom, {}, {})


@router.post(
    "/join",
    response_model=ClassroomDetail,
    summary="Join a class with an invite code",
)
async def join_classroom(
    payload: ClassroomJoinRequest,
    db: Session = Depends(get_db),
    student: User = Depends(StudentOnly),
) -> ClassroomDetail:
    """Enrol the calling student in the class that owns code."""
    classroom = (
        db.query(Classroom)
        .options(joinedload(Classroom.teacher), selectinload(Classroom.memberships))
        .filter(Classroom.code == payload.code)
        .one_or_none()
    )
    if classroom is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No class uses that invite code.",
        )
    if classroom.has_student(student.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You are already enrolled in {classroom.name}.",
        )

    db.add(ClassroomMembership(classroom_id=classroom.id, student_id=student.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You are already enrolled in {classroom.name}.",
        ) from None
    logger.info("student id=%s joined %s", student.id, classroom.code)
    return _classroom_detail(db, classroom)


@router.get(
    "/{classroom_id}",
    response_model=ClassroomDetail,
    summary="Class detail with roster",
)
async def read_classroom(
    classroom_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ClassroomDetail:
    """Class details, the enrolled roster and the problems published to it."""
    classroom = _load_classroom(db, classroom_id)
    _assert_classroom_visible(classroom, user)
    return _classroom_detail(db, classroom)


@router.get(
    "/{classroom_id}/gradebook.csv",
    response_class=Response,
    summary="Class gradebook as a CSV export",
)
async def classroom_gradebook_csv(
    classroom_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> Response:
    """Export one class's gradebook as a spreadsheet-ready CSV download."""
    classroom = _load_classroom(db, classroom_id)
    if not user.is_admin and classroom.teacher_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not teach that class.",
        )

    students = (
        db.query(User)
        .join(ClassroomMembership, ClassroomMembership.student_id == User.id)
        .filter(ClassroomMembership.classroom_id == classroom.id)
        .order_by(User.id)
        .all()
    )
    problems = (
        db.query(Problem)
        .filter(Problem.classroom_id == classroom.id)
        .order_by(Problem.id)
        .all()
    )

    grades: dict[tuple[int, int], tuple[float, int]] = {}
    if students and problems:
        rows = (
            db.query(Submission)
            .filter(
                Submission.problem_id.in_([problem.id for problem in problems]),
                Submission.student_id.in_([student.id for student in students]),
            )
            .order_by(Submission.id)
            .all()
        )
        for row in rows:
            key = (row.student_id, row.problem_id)
            score = _gradebook_effective_score(row)
            best, attempts = grades.get(key, (0.0, 0))
            grades[key] = (score if score >= best else best, attempts + 1)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    header = ["student_id", "username", "full_name", "email"]
    for problem in problems:
        label = problem.problem_code or f"P{problem.id}"
        header.append(_csv_cell(f"{label}_{problem.title}_score"))
        header.append(_csv_cell(f"{label}_attempts"))
    header.extend(["average_score", "total_score"])
    writer.writerow(header)

    problem_ids = [problem.id for problem in problems]
    for student in students:
        line = [
            student.id,
            _csv_cell(student.username),
            _csv_cell(student.full_name),
            _csv_cell(student.email),
        ]
        total = 0.0
        for problem_id in problem_ids:
            best, attempts = grades.get((student.id, problem_id), (0.0, 0))
            total += best
            line.append(round(best, 2))
            line.append(attempts)
        line.append(round(total / len(problem_ids), 2) if problem_ids else 0.0)
        line.append(round(total, 2))
        writer.writerow(line)

    logger.info(
        "staff %s exported the gradebook for class %s (%d students, %d problems)",
        user.id,
        classroom.code,
        len(students),
        len(problems),
    )
    return Response(
        content=("\ufeff" + buffer.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=gradebook_{classroom.code}.csv",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/{classroom_id}/problems/{problem_id}/plagiarism",
    response_model=PlagiarismReport,
    summary="Structural similarity screen for a class problem",
)
async def classroom_problem_plagiarism(
    classroom_id: int,
    problem_id: int,
    threshold: Annotated[
        float, Query(ge=0.0, le=1.0, description="Escalation cut-off for a pair.")
    ] = PLAGIARISM_DEFAULT_THRESHOLD,
    language: Optional[LanguageLiteral] = Query(
        default=None, description="Screen one language only; the default screens both."
    ),
    include_exempted: Annotated[
        bool,
        Query(
            description=(
                "Include threshold hits that Reference Baseline Subtraction "
                "cleared, so the report shows what was dismissed and why."
            )
        ),
    ] = True,
    db: Session = Depends(get_db),
    teacher: User = Depends(TeacherOnly),
) -> PlagiarismReport:
    """Rank the suspicious code pairs submitted to one problem of a class."""
    classroom = _load_classroom(db, classroom_id)
    _assert_classroom_visible(classroom, teacher)
    problem = _load_problem(db, problem_id)
    if problem.classroom_id != classroom.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This problem is not part of that class.",
        )
    if not _can_manage_problem(problem, teacher):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the teacher who owns a problem may screen it for plagiarism.",
        )

    member_ids = {membership.student_id for membership in classroom.memberships}
    filters = [Submission.problem_id == problem.id]
    if member_ids:
        filters.append(Submission.student_id.in_(member_ids))
    if language:
        filters.append(Submission.language == language)
    rows = (
        db.query(Submission)
        .options(joinedload(Submission.student))
        .filter(*filters)
        .order_by(Submission.score.desc(), Submission.created_at.desc(), Submission.id.desc())
        .all()
    )

    strongest: dict[tuple[int, str], Submission] = {}
    skipped_trivial = 0
    for row in rows:
        key = (row.student_id, row.language)
        if key in strongest:
            continue
        if _is_scaffolding(row.code, problem, row.language):
            skipped_trivial += 1
            continue
        strongest[key] = row

    selected = sorted(strongest.values(), key=lambda row: row.id)[:PLAGIARISM_MAX_SUBMISSIONS]
    truncated = len(strongest) > len(selected)

    groups: dict[str, dict[str, int]] = {}
    codes: dict[str, str] = {}
    for row in selected:
        label = _plagiarism_label(row)
        codes[label] = row.code or ""
        groups.setdefault(row.language, {})[label] = row.id

    pairs: list[PlagiarismPair] = []
    reference_available = bool(problem.ref_code and problem.ref_code.strip())
    for group_language, label_to_id in sorted(groups.items()):
        if len(label_to_id) < 2:
            continue
        entries = {label: codes[label] for label in label_to_id}
        measured = await run_in_threadpool(
            compute_similarity_pairs, entries, problem.ref_code or "", threshold
        )
        for entry in measured:
            flagged = bool(entry.get("flagged"))
            if not flagged and not include_exempted:
                continue
            label_a = str(entry["student_a"])
            label_b = str(entry["student_b"])
            pairs.append(
                PlagiarismPair(
                    student_a=label_a,
                    student_b=label_b,
                    submission_id_a=label_to_id[label_a],
                    submission_id_b=label_to_id[label_b],
                    language=group_language,
                    combined_similarity=float(entry["combined_similarity"]),
                    ast_similarity=float(entry["ast_similarity"]),
                    token_similarity=float(entry["token_similarity"]),
                    ngram_jaccard=float(entry.get("ngram_jaccard") or 0.0),
                    ref_similarity_a=entry.get("ref_similarity_a"),
                    ref_similarity_b=entry.get("ref_similarity_b"),
                    flagged=flagged,
                    exemption=entry.get("exemption"),
                )
            )

    pairs.sort(key=lambda pair: pair.combined_similarity, reverse=True)
    flagged_count = sum(1 for pair in pairs if pair.flagged)
    logger.info(
        "plagiarism screen: teacher %s problem %s threshold=%.2f compared=%d "
        "pairs=%d flagged=%d exempt=%d",
        teacher.id,
        problem.id,
        threshold,
        len(selected),
        len(pairs),
        flagged_count,
        len(pairs) - flagged_count,
    )
    return PlagiarismReport(
        problem_id=problem.id,
        problem_title=problem.title,
        problem_code=problem.problem_code,
        classroom_id=classroom.id,
        classroom_name=classroom.name,
        threshold=threshold,
        reference_available=reference_available,
        languages=sorted(groups),
        submissions_compared=len(selected),
        students_compared=len({row.student_id for row in selected}),
        pair_count=len(pairs),
        flagged_count=flagged_count,
        exempt_count=len(pairs) - flagged_count,
        skipped_trivial=skipped_trivial,
        truncated=truncated,
        pairs=pairs,
    )
